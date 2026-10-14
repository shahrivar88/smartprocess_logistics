"""Preflight of a ZIP container before anything inside it is read.

An `.xlsx` file is a ZIP archive, and an archive is hostile input until it has been measured.
This module reads only the **central directory**, which the archive states up front, so an
entry's declared size is known before a single byte is decompressed. Nothing here extracts an
entry to disk, and nothing here decompresses one at all.

What it refuses, and why each one is worth refusing:

- **Not an archive.** A file with an `.xlsx` name that is not a ZIP is rejected here rather
  than inside the reader.
- **Too many entries.** Bounds the work of iterating the directory.
- **Too much declared content.** The sum of declared uncompressed sizes, refused before
  anything is expanded.
- **A compression bomb.** An entry whose declared uncompressed size is many times its
  compressed size. A small file can declare gigabytes, and the declared size is what the
  reader would try to allocate.
- **An unsafe path.** An absolute path, a `..` segment, a backslash or a drive letter. Nothing
  is extracted, so this cannot write outside a directory, but `..` in an archive is never
  benign and refusing it removes the question.
- **Macros.** `vbaProject.bin` and the surrounding OLE parts. This app never needs a macro,
  and a macro is the whole reason `.xlsm` is a different format.
- **External links.** `xl/externalLinks/`. A workbook that references another file is either
  broken or trying to reach something, and the values this app reads never come from one.
- **Embedded objects.** `xl/embeddings/`, `xl/oleObjects/` and the OLE container files. An
  embedded object is a second document carrying its own parser.
- **Too many worksheets.** Bounds the number of readers that would be started.

The container is not the only thing read here
---------------------------------------------
Two small reads happen besides the central directory, and both exist to keep a *false* refusal
from being the price of a real one:

* the package's own content-type map (`[Content_Types].xml`), because a `.bin` part is printer
  settings only if the workbook **says** it is; and
* the first eight bytes of a `.bin` part that claims to be printer settings, because the suffix
  was standing in for a fact — "this is an OLE container" — and the fact itself is cheap to read.

Neither reads the sheet data, and both are bounded by the same checks as everything else: every
part is measured in the first pass, and the two reads happen **after** it, so a part that is too
big, too compressed, a macro or a traversing path is refused before anything is decompressed.
The first version of this read the content-type map before the measurements, which put a
whole-part decompression ahead of the very bound meant to catch it.

Both reads take the `ZipInfo` they were given rather than the part's name. A ZIP may hold two
members under one name, and opening by name answers with one of them — the last — however many
times it is asked. Reading each entry by identity means a package that hides an OLE container
behind a name it also uses for a benign part is refused on one of the two, which is the point of
looking at all.

`ArchiveReport` is returned only when every check passed, so a caller never has to reason
about a partially accepted archive.
"""

import io
import re
import zipfile
from dataclasses import dataclass

from smartprocess_logistics.core.units import describe_bytes

# A megabyte, as an integer, so the limits read as whole numbers.
MEGABYTE = 1024 * 1024

CODE_NOT_ARCHIVE = "ARCHIVE_NOT_A_ZIP"
CODE_EMPTY = "ARCHIVE_EMPTY"
CODE_FILE_TOO_LARGE = "ARCHIVE_FILE_TOO_LARGE"
CODE_TOO_MANY_ENTRIES = "ARCHIVE_TOO_MANY_ENTRIES"
CODE_TOO_LARGE_UNCOMPRESSED = "ARCHIVE_TOO_LARGE_UNCOMPRESSED"
CODE_COMPRESSION_BOMB = "ARCHIVE_COMPRESSION_BOMB"
CODE_UNSAFE_PATH = "ARCHIVE_UNSAFE_PATH"
CODE_MACROS = "ARCHIVE_MACROS_PRESENT"
CODE_EXTERNAL_LINKS = "ARCHIVE_EXTERNAL_LINKS"
CODE_EMBEDDED_OBJECTS = "ARCHIVE_EMBEDDED_OBJECTS"
CODE_TOO_MANY_SHEETS = "ARCHIVE_TOO_MANY_WORKSHEETS"
CODE_SHARED_STRINGS_TOO_LARGE = "ARCHIVE_SHARED_STRINGS_TOO_LARGE"

WORKSHEET_PREFIX = "xl/worksheets/"
SHARED_STRINGS_NAMES = ("xl/sharedstrings.xml",)
MACRO_PARTS = ("vbaproject", "xl/vbadata.xml", "xl/ctrlprops/")
EXTERNAL_LINK_PARTS = ("xl/externallinks/", "xl/externallink", "externallink")
EMBEDDED_PARTS = ("xl/embeddings/", "xl/oleobjects/", "xl/activex/")
# Suffixes that mean "a second document is in here" — except `.bin`, which is only suspicious
# until the package says what it holds. Excel writes legitimate binary parts of its own, and the
# one that matters is printer settings, written whenever a sheet is printed:
# `xl/printerSettings/printerSettings1.bin`, declared in the content-type map and referenced from
# the sheet's relationships. Refusing it by suffix refused a workbook nobody had done anything to.
OLE_SUFFIXES = (".bin", ".emf", ".wmf", ".xls", ".doc", ".ole")
# The magic number of an OLE compound file. This is what "embedded object" actually means, and it
# is a fact about the bytes rather than about a file name.
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
CONTENT_TYPES_NAME = "[content_types].xml"
PRINTER_SETTINGS_PREFIX = "xl/printersettings/"
PRINTER_SETTINGS_BIN_SUFFIX = ".bin"
PRINTER_SETTINGS_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.printersettings"

OVERRIDE_TAG = re.compile(r"<override\b([^>]*?)/?>", re.IGNORECASE)
ATTRIBUTE = re.compile(r'([a-zA-Z]+)\s*=\s*"([^"]*)"')


@dataclass(frozen=True)
class ArchiveLimits:
	"""The ceilings a container has to stay under."""

	max_file_bytes: int = 8 * MEGABYTE
	max_uncompressed_bytes: int = 64 * MEGABYTE
	max_compression_ratio: float = 100.0
	max_entries: int = 2000
	max_worksheets: int = 20
	# Every string in the workbook is pooled here, and the reader loads the whole pool before
	# it can hand back a single row. It is measured on its own so that a workbook with modest
	# sheets and a huge string pool is refused before the reader allocates it.
	max_shared_strings_bytes: int = 8 * MEGABYTE


@dataclass(frozen=True)
class ArchiveReport:
	"""What an accepted container contains, measured from its central directory."""

	entries: int
	uncompressed_bytes: int
	worksheets: tuple
	compressed_bytes: int
	shared_strings_bytes: int = 0

	@property
	def worksheet_count(self):
		return len(self.worksheets)


class ArchiveError(ValueError):
	"""A container that cannot be used. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


def _is_unsafe_path(name):
	"""Whether an entry name escapes the archive's own root."""
	if not name:
		return True

	normalised = name.replace("\\", "/")

	if normalised.startswith("/") or normalised.startswith("../"):
		return True
	if "\\" in name:
		return True
	if len(name) >= 2 and name[1] == ":":
		return True

	return ".." in normalised.split("/")


def _matches(lowered_name, parts):
	return any(part in lowered_name for part in parts)


def _printer_settings_parts(archive, entries):
	"""What this package declares as printer settings, read from its content-type map.

	Both declarations are honoured, because Excel writes both: a `Default` for the whole `bin`
	extension — which is what the workbook that prompted this carried — and an `Override` naming
	one part. A `bin` declared as anything else, or not declared at all, is not printer settings
	and is refused exactly as before.

	Returns `(declared part names, whether the whole bin extension is printer settings)`. A
	package whose content-type map cannot be read declares nothing, which fails closed.
	"""
	declared = set()
	whole_extension = False

	for entry in entries:
		if entry.filename.lower() != CONTENT_TYPES_NAME:
			continue

		try:
			with archive.open(entry) as stream:
				body = stream.read().decode("utf-8", "replace")
		except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError, MemoryError):
			return declared, whole_extension

		lowered = body.lower()

		if 'extension="bin"' in lowered and PRINTER_SETTINGS_CONTENT_TYPE in lowered:
			whole_extension = True

		for attributes in OVERRIDE_TAG.findall(body):
			fields = {key.lower(): value for key, value in ATTRIBUTE.findall(attributes)}
			content_type = (fields.get("contenttype") or "").lower()
			part = (fields.get("partname") or "").lstrip("/")

			if part and content_type == PRINTER_SETTINGS_CONTENT_TYPE:
				declared.add(part.lower())

		break

	return declared, whole_extension


def _is_declared_printer_settings(entry, archive, declared, whole_extension):
	"""Whether a `.bin` part is the printer settings its package says it is.

	Three kinds of evidence, and all three are needed. The part sits where Excel puts printer
	settings; the package declares it as printer settings; and its bytes are **not** an OLE
	container — which is the fact the suffix was standing in for, and the one that catches a
	renamed embedded object. Any of the three failing means the part is refused, so nothing here
	widens what an archive may carry except for the case that was wrong to refuse.

	`entry` is the member itself rather than its name, so a package that holds two members under
	one name has both of them looked at: opening by name answers with the same bytes however many
	times it is asked, and the one it answers with is not always the one being asked about.
	"""
	lowered = entry.filename.lower()

	if not lowered.endswith(PRINTER_SETTINGS_BIN_SUFFIX):
		return False

	if not lowered.startswith(PRINTER_SETTINGS_PREFIX):
		return False

	if lowered.lstrip("/") not in declared and not whole_extension:
		return False

	try:
		with archive.open(entry) as stream:
			return stream.read(len(OLE_MAGIC)) != OLE_MAGIC
	except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError, MemoryError):
		return False


def inspect_archive(data, limits=None):
	"""Measure and accept or refuse a ZIP container.

	Returns an `ArchiveReport`. Raises `ArchiveError` with a stable code otherwise.
	"""
	limits = limits or ArchiveLimits()

	if not isinstance(data, (bytes, bytearray)):
		raise ArchiveError(CODE_NOT_ARCHIVE, "محتوای فایل باید بایت باشد.")

	if not data:
		raise ArchiveError(CODE_EMPTY, "فایل خالی است.")

	if len(data) > limits.max_file_bytes:
		raise ArchiveError(
			CODE_FILE_TOO_LARGE,
			f"حجم فایل بیش از {describe_bytes(limits.max_file_bytes)} است.",
		)

	try:
		archive = zipfile.ZipFile(io.BytesIO(data))
		entries = archive.infolist()
	except zipfile.BadZipFile as error:
		raise ArchiveError(CODE_NOT_ARCHIVE, "فایل یک آرشیو ZIP معتبر نیست.") from error

	if len(entries) > limits.max_entries:
		raise ArchiveError(
			CODE_TOO_MANY_ENTRIES,
			f"تعداد ورودی‌های آرشیو بیش از {limits.max_entries} است.",
		)

	total_uncompressed = 0
	total_compressed = 0
	shared_strings_bytes = 0
	worksheets = []
	# The `xl/**.bin` parts, kept for the second pass: whether one of them is printer settings is
	# decided by reading the package's content-type map, and that read belongs *after* these
	# measurements rather than before them.
	ole_suffixed = []

	for entry in entries:
		name = entry.filename
		lowered = name.lower()

		if _is_unsafe_path(name):
			raise ArchiveError(CODE_UNSAFE_PATH, f"مسیر ناایمن در آرشیو: {name!r}")

		if _matches(lowered, MACRO_PARTS):
			raise ArchiveError(CODE_MACROS, "فایل دارای ماکرو است.")

		if _matches(lowered, EXTERNAL_LINK_PARTS):
			raise ArchiveError(CODE_EXTERNAL_LINKS, "فایل به منابع خارجی ارجاع می‌دهد.")

		if _matches(lowered, EMBEDDED_PARTS):
			raise ArchiveError(CODE_EMBEDDED_OBJECTS, "فایل دارای شیء جاسازی‌شده است.")

		if lowered.startswith("xl/") and lowered.endswith(OLE_SUFFIXES):
			ole_suffixed.append(entry)

		if lowered.startswith(WORKSHEET_PREFIX) and lowered.endswith(".xml"):
			worksheets.append(name)

		if lowered in SHARED_STRINGS_NAMES:
			shared_strings_bytes += entry.file_size

			if shared_strings_bytes > limits.max_shared_strings_bytes:
				raise ArchiveError(
					CODE_SHARED_STRINGS_TOO_LARGE,
					"مخزن رشته‌های فایل بیش از حد بزرگ است.",
				)

		total_uncompressed += entry.file_size
		total_compressed += entry.compress_size

		if total_uncompressed > limits.max_uncompressed_bytes:
			raise ArchiveError(
				CODE_TOO_LARGE_UNCOMPRESSED,
				f"حجم باز‌شدهٔ آرشیو بیش از {describe_bytes(limits.max_uncompressed_bytes)} است.",
			)

		if entry.compress_size > 0:
			ratio = entry.file_size / entry.compress_size
			if ratio > limits.max_compression_ratio:
				raise ArchiveError(
					CODE_COMPRESSION_BOMB,
					f"نسبت فشرده‌سازی ورودی {name!r} غیرعادی است.",
				)

	if len(worksheets) > limits.max_worksheets:
		raise ArchiveError(
			CODE_TOO_MANY_SHEETS,
			f"تعداد شیت‌های فایل بیش از {limits.max_worksheets} است.",
		)

	# Every part has been measured by now, so the content-type map is read on a package that has
	# already been bounded: a map that is itself a bomb is refused above, before it is decompressed.
	if ole_suffixed:
		declared, whole_extension = _printer_settings_parts(archive, entries)

		for entry in ole_suffixed:
			if not _is_declared_printer_settings(entry, archive, declared, whole_extension):
				raise ArchiveError(CODE_EMBEDDED_OBJECTS, "فایل دارای شیء جاسازی‌شده است.")

	return ArchiveReport(
		entries=len(entries),
		uncompressed_bytes=total_uncompressed,
		worksheets=tuple(worksheets),
		compressed_bytes=total_compressed,
		shared_strings_bytes=shared_strings_bytes,
	)
