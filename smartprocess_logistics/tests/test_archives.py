"""Archive preflight, exercised with archives built in memory.

Nothing is written to disk and nothing is extracted. Each hostile archive is built with
`zipfile` from bytes the test controls, so the refusal is tested against a real archive rather
than a mock.
"""

import io
import warnings
import zipfile

from smartprocess_logistics.core.archives import (
	CODE_COMPRESSION_BOMB,
	CODE_EMBEDDED_OBJECTS,
	CODE_EMPTY,
	CODE_EXTERNAL_LINKS,
	CODE_FILE_TOO_LARGE,
	CODE_MACROS,
	CODE_NOT_ARCHIVE,
	CODE_SHARED_STRINGS_TOO_LARGE,
	CODE_TOO_LARGE_UNCOMPRESSED,
	CODE_TOO_MANY_ENTRIES,
	CODE_TOO_MANY_SHEETS,
	CODE_UNSAFE_PATH,
	ArchiveError,
	ArchiveLimits,
	inspect_archive,
)
from smartprocess_logistics.tests.base import UnitTestCase


def build_archive(entries, compression=zipfile.ZIP_DEFLATED):
	"""A ZIP in memory. `entries` maps a name to its bytes."""
	buffer = io.BytesIO()

	with zipfile.ZipFile(buffer, "w", compression) as archive:
		for name, content in entries.items():
			archive.writestr(name, content)

	return buffer.getvalue()


def minimal_workbook_bytes(extra=None):
	entries = {
		"[Content_Types].xml": b"<Types/>",
		"xl/workbook.xml": b"<workbook/>",
		"xl/worksheets/sheet1.xml": b"<worksheet/>",
	}
	entries.update(extra or {})

	return build_archive(entries)


class TestAcceptedArchives(UnitTestCase):
	def test_a_minimal_workbook_is_accepted(self):
		report = inspect_archive(minimal_workbook_bytes())

		self.assertEqual(report.worksheets, ("xl/worksheets/sheet1.xml",))
		self.assertEqual(report.worksheet_count, 1)
		self.assertGreater(report.entries, 0)

	def test_compressed_and_uncompressed_sizes_are_measured(self):
		report = inspect_archive(build_archive({"a.txt": b"x" * 500}))

		self.assertGreaterEqual(report.uncompressed_bytes, 500)

	def test_custom_limits_are_honoured(self):
		report = inspect_archive(minimal_workbook_bytes(), limits=ArchiveLimits(max_entries=50))

		self.assertGreater(report.entries, 0)


class TestNotAnArchive(UnitTestCase):
	def test_random_bytes_are_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(b"this is not a zip file at all")

		self.assertEqual(caught.exception.code, CODE_NOT_ARCHIVE)

	def test_a_truncated_archive_is_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(minimal_workbook_bytes()[:40])

		self.assertEqual(caught.exception.code, CODE_NOT_ARCHIVE)

	def test_empty_content_is_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(b"")

		self.assertEqual(caught.exception.code, CODE_EMPTY)

	def test_non_bytes_are_refused(self):
		for value in (None, "text", 5, []):
			with self.subTest(value=value):
				with self.assertRaises(ArchiveError) as caught:
					inspect_archive(value)

				self.assertEqual(caught.exception.code, CODE_NOT_ARCHIVE)


class TestLimits(UnitTestCase):
	def test_too_many_entries(self):
		entries = {f"f{index}.txt": b"x" for index in range(10)}

		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(build_archive(entries), limits=ArchiveLimits(max_entries=5))

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_ENTRIES)

	def test_too_much_declared_content(self):
		entries = {f"f{index}.txt": b"x" * 10_000 for index in range(5)}

		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(build_archive(entries), limits=ArchiveLimits(max_uncompressed_bytes=1000))

		self.assertEqual(caught.exception.code, CODE_TOO_LARGE_UNCOMPRESSED)

	def test_an_oversized_file_is_refused_before_it_is_opened(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(minimal_workbook_bytes(), limits=ArchiveLimits(max_file_bytes=10))

		self.assertEqual(caught.exception.code, CODE_FILE_TOO_LARGE)

	def test_a_compression_bomb_is_refused(self):
		# A megabyte of zeros compresses to about a kilobyte, so one entry declares far more
		# content than it carries.
		bomb = build_archive({"xl/worksheets/sheet1.xml": b"\x00" * (1024 * 1024)})

		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(bomb, limits=ArchiveLimits(max_compression_ratio=10.0))

		self.assertEqual(caught.exception.code, CODE_COMPRESSION_BOMB)

	def test_a_high_ratio_but_small_entry_is_allowed_by_the_default_ratio(self):
		# The default ratio is 100, and an honest worksheet is nowhere near it.
		report = inspect_archive(minimal_workbook_bytes())

		self.assertGreater(report.entries, 0)

	def test_too_many_worksheets(self):
		entries = {f"xl/worksheets/sheet{index}.xml": b"<worksheet/>" for index in range(5)}

		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(build_archive(entries), limits=ArchiveLimits(max_worksheets=2))

		self.assertEqual(caught.exception.code, CODE_TOO_MANY_SHEETS)


class TestPrinterSettingsParts(UnitTestCase):
	"""The `.bin` Excel writes when a sheet is printed, and the `.bin` that is a second document.

	A workbook exported from a printed sheet carries `xl/printerSettings/printerSettings1.bin`,
	declared in the content-type map and referenced from the sheet's relationships. Refusing every
	`.bin` refused that workbook — it was reported by an operator whose file had been produced by
	Excel and nothing else. What follows pins both halves of the correction: the part Excel writes
	is accepted, and every shape an embedded object can take is still refused.
	"""

	PRINTER_PART = "xl/printerSettings/printerSettings1.bin"
	# The bytes Excel wrote into the workbook this rule was corrected for: the "Foxi" printer
	# settings that came with it, which begin with UTF-16 text and are not an OLE container.
	PRINTER_BYTES = b"F\x00o\x00x\x00i\x00 \x00p\x00r\x00i\x00n\x00t\x00e\x00r\x00"

	DECLARED_WHOLE_EXTENSION = (
		b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
		b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
		b'<Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument'
		b'.spreadsheetml.printerSettings"/>'
		b'<Default Extension="xml" ContentType="application/xml"/></Types>'
	)

	DECLARED_AS_SOMETHING_ELSE = (
		b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
		b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
		b'<Default Extension="bin" ContentType="application/vnd.ms-office.oleObject"/>'
		b'<Default Extension="xml" ContentType="application/xml"/></Types>'
	)

	def workbook(self, content_types, extra=None):
		entries = {
			"[Content_Types].xml": content_types,
			"xl/workbook.xml": b"<workbook/>",
			"xl/worksheets/sheet1.xml": b"<worksheet/>",
			"xl/worksheets/_rels/sheet1.xml.rels": (
				b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
				b'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006'
				b'/relationships/printerSettings" Target="../printerSettings/printerSettings1.bin"/>'
				b"</Relationships>"
			),
		}
		entries.update(extra or {})

		return build_archive(entries)

	def workbook_with_duplicate_members(self, content_types, members):
		"""A ZIP holding the same part name more than once, which `build_archive` cannot express.

		`zipfile` writes them happily, and a reader that opens a part *by name* answers with one of
		them — the last — however many times it is asked. That is the shape an archive would use to
		keep one set of bytes out of the preflight's sight, so the members are written here by hand.
		"""
		buffer = io.BytesIO()

		with warnings.catch_warnings():
			# `zipfile` warns about the duplicate name it is being asked to write, which is the
			# thing this fixture exists to produce. The warning is for people who did it by
			# accident.
			warnings.simplefilter("ignore", UserWarning)

			with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
				archive.writestr("[Content_Types].xml", content_types)
				archive.writestr("xl/workbook.xml", b"<workbook/>")
				archive.writestr("xl/worksheets/sheet1.xml", b"<worksheet/>")

				for name, content in members:
					archive.writestr(name, content)

		return buffer.getvalue()

	def test_a_content_type_map_that_is_itself_a_bomb_is_refused_by_the_bound(self):
		"""The map is read like every other part, and the bound is applied to it.

		The read of `[Content_Types].xml` is the one part this module opens besides the eight bytes
		of a printer-settings `.bin`, so it is the only place where the order of "measure" and
		"read" could be got wrong — and it was: the map was decompressed before the checks that
		bound it, which is exactly the part a bomb would arrive in. A package whose map inflates
		to two megabytes from two hundred compressed bytes is refused for its ratio, and refused
		before anything of it is decompressed.
		"""
		bomb = b"<Types><!--" + (b"\x00" * (2 * 1024 * 1024)) + b"-->"

		code = self.refused(self.workbook(bomb, {self.PRINTER_PART: self.PRINTER_BYTES}))

		self.assertEqual(code, CODE_COMPRESSION_BOMB)

	def test_a_large_but_honest_content_type_map_is_still_read(self):
		"""The bound is a bound, not a refusal of every map bigger than the ones we have seen."""
		map_with_room = (
			b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
			b'<Default Extension="xml" ContentType="application/xml"/>'
			b'<Default Extension="bin" ContentType="application/vnd.openxmlformats-officedocument'
			b'.spreadsheetml.printerSettings"/>' + b" " * 4096 + b"</Types>"
		)

		report = inspect_archive(self.workbook(map_with_room, {self.PRINTER_PART: self.PRINTER_BYTES}))

		self.assertEqual(report.worksheets, ("xl/worksheets/sheet1.xml",))

	def test_an_ole_member_hidden_behind_a_benign_name_is_refused(self):
		"""Two members, one name, one of them a container: whichever order they are written in."""
		ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64

		for members in (
			((self.PRINTER_PART, self.PRINTER_BYTES), (self.PRINTER_PART, ole)),
			((self.PRINTER_PART, ole), (self.PRINTER_PART, self.PRINTER_BYTES)),
		):
			with self.subTest(first=members[0][1][:4]):
				code = self.refused(
					self.workbook_with_duplicate_members(self.DECLARED_WHOLE_EXTENSION, members)
				)

				self.assertEqual(code, CODE_EMBEDDED_OBJECTS)

	def refused(self, data):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(data)

		return caught.exception.code

	def test_the_part_excel_writes_for_printer_settings_is_accepted(self):
		"""The file that prompted this: a declared printerSettings `.bin`, and nothing else odd."""
		report = inspect_archive(
			self.workbook(self.DECLARED_WHOLE_EXTENSION, {self.PRINTER_PART: self.PRINTER_BYTES})
		)

		self.assertEqual(report.worksheets, ("xl/worksheets/sheet1.xml",))

	def test_a_part_declared_by_name_is_accepted_too(self):
		"""Excel writes either declaration, depending on the version that saved the file."""
		content_types = (
			b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
			b'<Default Extension="xml" ContentType="application/xml"/>'
			b'<Override PartName="/xl/printerSettings/printerSettings1.bin" ContentType='
			b'"application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings"/></Types>'
		)

		inspect_archive(self.workbook(content_types, {self.PRINTER_PART: self.PRINTER_BYTES}))

	def test_a_bin_the_workbook_never_declared_is_still_refused(self):
		"""Nothing is whitelisted by suffix: without the declaration it is not printer settings."""
		code = self.refused(
			self.workbook(self.DECLARED_AS_SOMETHING_ELSE, {self.PRINTER_PART: self.PRINTER_BYTES})
		)

		self.assertEqual(code, CODE_EMBEDDED_OBJECTS)

	def test_a_bin_declared_as_printer_settings_but_holding_an_ole_container_is_refused(self):
		"""The shape a renamed embedded object would have, and the reason the bytes are read."""
		ole = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64

		code = self.refused(self.workbook(self.DECLARED_WHOLE_EXTENSION, {self.PRINTER_PART: ole}))

		self.assertEqual(code, CODE_EMBEDDED_OBJECTS)

	def test_a_bin_outside_the_printer_settings_folder_is_refused(self):
		"""Being declared is not enough: printer settings live where Excel puts them."""
		code = self.refused(
			self.workbook(self.DECLARED_WHOLE_EXTENSION, {"xl/media/payload.bin": self.PRINTER_BYTES})
		)

		self.assertEqual(code, CODE_EMBEDDED_OBJECTS)

	def test_an_embedded_object_under_embeddings_is_refused_whatever_it_is_called(self):
		for name in ("xl/embeddings/oleObject1.bin", "xl/embeddings/whatever.dat"):
			with self.subTest(name=name):
				code = self.refused(self.workbook(self.DECLARED_WHOLE_EXTENSION, {name: self.PRINTER_BYTES}))

				self.assertEqual(code, CODE_EMBEDDED_OBJECTS)

	def test_the_container_suffixes_are_refused_as_they_were(self):
		for name in (
			"xl/media/image.emf",
			"xl/media/image.wmf",
			"xl/embeddings/embedded.xls",
			"xl/embeddings/embedded.doc",
			"xl/embeddings/embedded.ole",
		):
			with self.subTest(name=name):
				code = self.refused(self.workbook(self.DECLARED_WHOLE_EXTENSION, {name: self.PRINTER_BYTES}))

				self.assertEqual(code, CODE_EMBEDDED_OBJECTS)

	def test_a_workbook_with_no_content_type_map_still_refuses_its_bin(self):
		"""A package that declares nothing declares nothing: the part is refused, as before."""
		code = self.refused(minimal_workbook_bytes({self.PRINTER_PART: self.PRINTER_BYTES}))

		self.assertEqual(code, CODE_EMBEDDED_OBJECTS)


class TestHostileContent(UnitTestCase):
	def test_macros_are_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(minimal_workbook_bytes({"xl/vbaProject.bin": b"\x00"}))

		self.assertEqual(caught.exception.code, CODE_MACROS)

	def test_an_absolute_path_is_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(build_archive({"/etc/passwd": b"x"}))

		self.assertEqual(caught.exception.code, CODE_UNSAFE_PATH)

	def test_a_parent_segment_is_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(build_archive({"../outside.txt": b"x"}))

		self.assertEqual(caught.exception.code, CODE_UNSAFE_PATH)

	def test_a_nested_parent_segment_is_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(build_archive({"xl/../../outside.txt": b"x"}))

		self.assertEqual(caught.exception.code, CODE_UNSAFE_PATH)

	def test_a_backslash_path_is_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(build_archive({"xl\\..\\outside.txt": b"x"}))

		self.assertEqual(caught.exception.code, CODE_UNSAFE_PATH)

	def test_external_links_are_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(minimal_workbook_bytes({"xl/externalLinks/externalLink1.xml": b"<x/>"}))

		self.assertEqual(caught.exception.code, CODE_EXTERNAL_LINKS)

	def test_embedded_objects_are_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(minimal_workbook_bytes({"xl/embeddings/oleObject1.bin": b"\x00"}))

		self.assertEqual(caught.exception.code, CODE_EMBEDDED_OBJECTS)

	def test_an_ole_container_under_xl_is_refused(self):
		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(minimal_workbook_bytes({"xl/media/embedded.wmf": b"\x00"}))

		self.assertEqual(caught.exception.code, CODE_EMBEDDED_OBJECTS)

	def test_a_benign_image_is_not_treated_as_an_embedded_object(self):
		report = inspect_archive(minimal_workbook_bytes({"xl/media/image1.png": b"\x89PNG"}))

		self.assertGreater(report.entries, 0)


class TestSharedStrings(UnitTestCase):
	"""The reader loads the whole string pool before it returns a single row.

	The pool is measured on its own so a workbook with modest sheets and an enormous pool is
	refused before the reader allocates it.
	"""

	def test_the_pool_is_measured(self):
		report = inspect_archive(minimal_workbook_bytes({"xl/sharedStrings.xml": b"x" * 500}))

		self.assertGreaterEqual(report.shared_strings_bytes, 500)

	def test_a_workbook_with_no_pool_reports_zero(self):
		self.assertEqual(inspect_archive(minimal_workbook_bytes()).shared_strings_bytes, 0)

	def test_an_oversized_pool_is_refused(self):
		entries = {"xl/sharedStrings.xml": b"x" * 5000, "xl/worksheets/sheet1.xml": b"<worksheet/>"}

		with self.assertRaises(ArchiveError) as caught:
			inspect_archive(
				build_archive(entries),
				limits=ArchiveLimits(max_shared_strings_bytes=1000, max_uncompressed_bytes=10_000_000),
			)

		self.assertEqual(caught.exception.code, CODE_SHARED_STRINGS_TOO_LARGE)

	def test_the_pool_limit_does_not_affect_a_normal_workbook(self):
		report = inspect_archive(minimal_workbook_bytes({"xl/sharedStrings.xml": b"<sst/>"}))

		self.assertGreater(report.entries, 0)
