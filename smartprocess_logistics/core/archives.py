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

`ArchiveReport` is returned only when every check passed, so a caller never has to reason
about a partially accepted archive.
"""

import io
import zipfile
from dataclasses import dataclass

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
OLE_SUFFIXES = (".bin", ".emf", ".wmf", ".xls", ".doc", ".ole")


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
			f"حجم فایل بیش از {limits.max_file_bytes // MEGABYTE} مگابایت است.",
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

	for entry in entries:
		name = entry.filename
		lowered = name.lower()

		if _is_unsafe_path(name):
			raise ArchiveError(CODE_UNSAFE_PATH, f"مسیر ناایمن در آرشیو: {name!r}")

		if _matches(lowered, MACRO_PARTS):
			raise ArchiveError(CODE_MACROS, "فایل دارای ماکرو است.")

		if _matches(lowered, EXTERNAL_LINK_PARTS):
			raise ArchiveError(CODE_EXTERNAL_LINKS, "فایل به منابع خارجی ارجاع می‌دهد.")

		if _matches(lowered, EMBEDDED_PARTS) or (
			lowered.startswith("xl/") and lowered.endswith(OLE_SUFFIXES)
		):
			raise ArchiveError(CODE_EMBEDDED_OBJECTS, "فایل دارای شیء جاسازی‌شده است.")

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
				f"حجم باز‌شدهٔ آرشیو بیش از {limits.max_uncompressed_bytes // MEGABYTE} مگابایت است.",
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

	return ArchiveReport(
		entries=len(entries),
		uncompressed_bytes=total_uncompressed,
		worksheets=tuple(worksheets),
		compressed_bytes=total_compressed,
		shared_strings_bytes=shared_strings_bytes,
	)
