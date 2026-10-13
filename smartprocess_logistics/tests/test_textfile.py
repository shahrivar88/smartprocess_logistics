"""Decoding a text file, and refusing the ones that are not text."""

import base64

from smartprocess_logistics.core.textfile import (
	CODE_BAD_BASE64,
	CODE_BAD_DATA_URL,
	CODE_BINARY,
	CODE_EMPTY,
	CODE_NOT_BYTES,
	CODE_NOT_PAYLOAD,
	CODE_TOO_LARGE,
	CODE_UNDECODABLE,
	CODE_UTF16,
	DEFAULT_ENCODINGS,
	MAX_TEXT_BYTES,
	TextFileError,
	decode_text,
	detect_utf16,
	payload_bytes,
)
from smartprocess_logistics.tests.base import UnitTestCase


class TestAcceptedFiles(UnitTestCase):
	def test_plain_utf8(self):
		decoded = decode_text("A1,B2".encode())

		self.assertEqual(decoded.text, "A1,B2")
		self.assertEqual(decoded.encoding, "utf-8-sig")
		self.assertEqual(decoded.bytes_read, 5)

	def test_persian_utf8(self):
		decoded = decode_text("سریال ۱".encode())

		self.assertEqual(decoded.text, "سریال ۱")

	def test_a_byte_order_mark_is_stripped_by_the_encoding(self):
		decoded = decode_text(b"\xef\xbb\xbfA1")

		self.assertEqual(decoded.text, "A1")

	def test_a_trailing_newline_is_kept(self):
		# The splitter deals with the empty token it produces; the decoder does not hide it.
		self.assertEqual(decode_text(b"A1\n").text, "A1\n")

	def test_the_default_encoding_list_is_utf8_only(self):
		self.assertEqual(DEFAULT_ENCODINGS, ("utf-8-sig",))

	def test_an_explicit_fallback_may_be_passed(self):
		# windows-1256 is not a default, because it decodes almost anything to mojibake. A
		# caller that knows its file may ask for it. The sample uses the Arabic yeh, which is
		# the one code page 1256 actually carries.
		source = "سريال"
		decoded = decode_text(source.encode("windows-1256"), encodings=("utf-8-sig", "windows-1256"))

		self.assertEqual(decoded.encoding, "windows-1256")
		self.assertEqual(decoded.text, source)


class TestRefusedFiles(UnitTestCase):
	def test_empty_content(self):
		with self.assertRaises(TextFileError) as caught:
			decode_text(b"")

		self.assertEqual(caught.exception.code, CODE_EMPTY)

	def test_non_bytes(self):
		for value in (None, "text", 5, []):
			with self.subTest(value=value):
				with self.assertRaises(TextFileError) as caught:
					decode_text(value)

				self.assertEqual(caught.exception.code, CODE_NOT_BYTES)

	def test_oversized_content(self):
		with self.assertRaises(TextFileError) as caught:
			decode_text(b"x" * (MAX_TEXT_BYTES + 1))

		self.assertEqual(caught.exception.code, CODE_TOO_LARGE)

	def test_a_custom_size_limit(self):
		with self.assertRaises(TextFileError) as caught:
			decode_text(b"x" * 100, max_bytes=10)

		self.assertEqual(caught.exception.code, CODE_TOO_LARGE)

	def test_a_utf16_byte_order_mark_is_reported_by_name(self):
		for mark in (b"\xff\xfe", b"\xfe\xff"):
			with self.subTest(mark=mark):
				with self.assertRaises(TextFileError) as caught:
					decode_text(mark + b"A\x00")

				self.assertEqual(caught.exception.code, CODE_UTF16)
				self.assertIn("UTF-8", caught.exception.message)

	def test_a_null_byte_means_the_content_is_not_text(self):
		with self.assertRaises(TextFileError) as caught:
			decode_text(b"PK\x03\x04\x00\x00binary")

		self.assertEqual(caught.exception.code, CODE_BINARY)

	def test_undecodable_bytes_report_every_encoding_tried(self):
		with self.assertRaises(TextFileError) as caught:
			decode_text(b"\xff\xfe\xfd\xfc")

		self.assertIn(caught.exception.code, (CODE_UTF16, CODE_BINARY, CODE_UNDECODABLE))

	def test_invalid_utf8_with_no_fallback_lists_utf8(self):
		with self.assertRaises(TextFileError) as caught:
			decode_text(b"A1\x80\x81B2")

		self.assertEqual(caught.exception.code, CODE_UNDECODABLE)
		self.assertIn("utf-8-sig", caught.exception.message)

	def test_the_message_names_the_offset_of_the_bad_byte(self):
		with self.assertRaises(TextFileError) as caught:
			decode_text(b"AB\x80")

		self.assertIn("2", caught.exception.message)


class TestUtf16Detection(UnitTestCase):
	def test_the_marks_are_detected(self):
		self.assertEqual(detect_utf16(b"\xff\xfeA"), b"\xff\xfe")
		self.assertEqual(detect_utf16(b"\xfe\xffA"), b"\xfe\xff")

	def test_plain_content_has_no_mark(self):
		self.assertIsNone(detect_utf16(b"A1,B2"))
		self.assertIsNone(detect_utf16(b""))


WORKBOOK_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class TestPayloadBytes(UnitTestCase):
	"""A browser cannot put bytes into a JSON request, so a workbook arrives as a data URL.

	Until this existed, `content` was encoded as UTF-8 whatever the file was, so an `.xlsx`
	reached the workbook reader as mangled text. The point of the check is that the decode is
	refused when it is wrong: guessing here produces something that looks like data and is not.
	"""

	def data_url(self, payload, media_type=WORKBOOK_MEDIA_TYPE):
		return f"data:{media_type};base64," + base64.b64encode(payload).decode()

	def test_bytes_pass_through_untouched(self):
		payload = b"PK" + bytes(range(256))

		self.assertEqual(payload_bytes(payload), payload)

	def test_text_is_encoded_as_utf8(self):
		self.assertEqual(payload_bytes("سلام"), "سلام".encode())

	def test_a_base64_data_url_is_decoded(self):
		payload = b"PK" + bytes(range(256)) * 4

		self.assertEqual(payload_bytes(self.data_url(payload)), payload)

	def test_a_data_url_without_base64_is_refused(self):
		with self.assertRaises(TextFileError) as caught:
			payload_bytes("data:text/plain,hello")

		self.assertEqual(caught.exception.code, CODE_BAD_DATA_URL)

	def test_a_data_url_with_no_payload_is_refused(self):
		with self.assertRaises(TextFileError) as caught:
			payload_bytes("data:text/plain;base64")

		self.assertEqual(caught.exception.code, CODE_BAD_DATA_URL)

	def test_broken_base64_is_refused(self):
		with self.assertRaises(TextFileError) as caught:
			payload_bytes("data:application/octet-stream;base64,not*valid*base64!")

		self.assertEqual(caught.exception.code, CODE_BAD_BASE64)

	def test_a_payload_that_is_neither_text_nor_bytes_is_refused(self):
		for value in (None, 5, ["x"], {"a": 1}):
			with self.subTest(value=value), self.assertRaises(TextFileError) as caught:
				payload_bytes(value)

			self.assertEqual(caught.exception.code, CODE_NOT_PAYLOAD)

	def test_a_data_url_is_only_recognised_at_the_start(self):
		"""A serial list that merely contains the word data is still text."""
		newline = chr(10)
		text = "1*" + newline + "data:application/octet-stream;base64,AAAA" + newline

		self.assertEqual(payload_bytes(text), text.encode())
