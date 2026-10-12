"""Decoding a text file, and refusing the ones that are not text.

The default encoding list is deliberately short: **UTF-8 with or without a byte order mark,
and nothing else**. A single-byte encoding such as `windows-1256` decodes almost any byte
sequence to *something*, so putting it in the list would turn a wrong-encoding file into a
page of mojibake that looks like serial numbers instead of into a refusal. A caller that knows
its file is `windows-1256` may pass it explicitly; the default never guesses.

The checks run in the order that costs least and refuses most:

1. Byte length, before any decoding.
2. A UTF-16 byte order mark, reported by name, because "save it as UTF-8" is the fix and the
   operator cannot see the mark.
3. A NUL byte, which means the content is binary whatever it is named.
4. Decoding, in the caller's order, reporting every encoding that was tried.
"""

from dataclasses import dataclass

MEGABYTE = 1024 * 1024

DEFAULT_ENCODINGS = ("utf-8-sig",)
MAX_TEXT_BYTES = 4 * MEGABYTE

UTF16_BYTE_ORDER_MARKS = (b"\xff\xfe", b"\xfe\xff")

CODE_NOT_BYTES = "TEXT_NOT_BYTES"
CODE_EMPTY = "TEXT_EMPTY"
CODE_TOO_LARGE = "TEXT_TOO_LARGE"
CODE_BINARY = "TEXT_BINARY_CONTENT"
CODE_UTF16 = "TEXT_UTF16_NOT_SUPPORTED"
CODE_UNDECODABLE = "TEXT_UNDECODABLE"


class TextFileError(ValueError):
	"""A text file that cannot be used. `code` is stable."""

	def __init__(self, code, message):
		super().__init__(message)
		self.code = code
		self.message = message


@dataclass(frozen=True)
class DecodedText:
	"""The decoded content, and which encoding produced it."""

	text: str
	encoding: str
	bytes_read: int


def detect_utf16(data):
	"""Whether the content starts with a UTF-16 byte order mark."""
	for mark in UTF16_BYTE_ORDER_MARKS:
		if data.startswith(mark):
			return mark

	return None


def decode_text(data, encodings=DEFAULT_ENCODINGS, max_bytes=MAX_TEXT_BYTES):
	"""Decode text content, or raise `TextFileError` with a stable code."""
	if not isinstance(data, (bytes, bytearray)):
		raise TextFileError(CODE_NOT_BYTES, "محتوای فایل باید بایت باشد.")

	if not data:
		raise TextFileError(CODE_EMPTY, "فایل خالی است.")

	if len(data) > max_bytes:
		raise TextFileError(
			CODE_TOO_LARGE,
			f"حجم فایل بیش از {max_bytes // MEGABYTE} مگابایت است.",
		)

	if detect_utf16(data) is not None:
		raise TextFileError(
			CODE_UTF16,
			"فایل با کدگذاری UTF-16 ذخیره شده است. آن را با UTF-8 ذخیره کنید.",
		)

	if b"\x00" in data:
		raise TextFileError(CODE_BINARY, "محتوای فایل متنی نیست.")

	attempted = []

	for encoding in encodings:
		try:
			return DecodedText(
				text=bytes(data).decode(encoding),
				encoding=encoding,
				bytes_read=len(data),
			)
		except UnicodeDecodeError as error:
			attempted.append(f"{encoding} (بایت {error.start})")

	raise TextFileError(
		CODE_UNDECODABLE,
		"فایل با کدگذاری‌های پشتیبانی‌شده خوانده نشد: " + "، ".join(attempted),
	)
