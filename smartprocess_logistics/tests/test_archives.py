"""Archive preflight, exercised with archives built in memory.

Nothing is written to disk and nothing is extracted. Each hostile archive is built with
`zipfile` from bytes the test controls, so the refusal is tested against a real archive rather
than a mock.
"""

import io
import zipfile

from smartprocess_logistics.core.archives import (
	CODE_COMPRESSION_BOMB,
	CODE_EMBEDDED_OBJECTS,
	CODE_EMPTY,
	CODE_EXTERNAL_LINKS,
	CODE_FILE_TOO_LARGE,
	CODE_SHARED_STRINGS_TOO_LARGE,
	CODE_MACROS,
	CODE_NOT_ARCHIVE,
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
