"""Shared attachment policy.

Single source of truth for which file types may be attached to an email
and how a file's content is checked against its extension.

Both the multipart upload endpoint (POST /api/emails/<id>/attachments) and
the raw MIME endpoint (POST /api/emails/mime) use this module, so the two
send paths accept and reject exactly the same files.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Extensions that may be attached. This covers the document, spreadsheet,
# image, archive and media formats a mail client realistically receives.
ALLOWED_EXTENSIONS = {
	# text / data
	'txt', 'md', 'csv', 'tsv', 'json', 'xml', 'yaml', 'yml', 'html', 'htm', 'rtf',
	# documents
	'pdf', 'doc', 'docx', 'odt', 'odp', 'ods', 'epub',
	# spreadsheets
	'xls', 'xlsx', 'xlsm', 'xlsb', 'numbers',
	# presentations
	'ppt', 'pptx',
	# images
	'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tif', 'tiff', 'heic', 'heif',
	# archives
	'zip', 'gz', 'tgz', 'tar', 'bz2', 'xz', '7z', 'rar',
	# mail / calendar
	'eml', 'msg', 'ics',
	# media
	'mp3', 'm4a', 'wav', 'mp4', 'mov', 'm4v',
	# certificates / keys
	'pem', 'p12', 'pfx', 'cer', 'crt', 'key',
	# accounting / business
	'aba', 'qbo', 'qif',
}

# Expected MIME type prefixes per extension. A file whose detected MIME type
# does not match its extension is rejected. The comparison also accepts any
# subtype of the listed major type (see validate_bytes).
EXTENSION_MIME_MAP = {
	'txt': ['text/plain'],
	'md': ['text/plain', 'text/markdown'],
	'csv': ['text/csv', 'text/plain'],
	'tsv': ['text/tab-separated-values', 'text/plain'],
	'json': ['application/json', 'text/plain'],
	'xml': ['text/xml', 'application/xml', 'text/plain'],
	'yaml': ['text/plain', 'application/x-yaml'],
	'yml': ['text/plain', 'application/x-yaml'],
	'html': ['text/html', 'text/plain'],
	'htm': ['text/html', 'text/plain'],
	'rtf': ['application/rtf', 'text/rtf'],
	'pdf': ['application/pdf'],
	'doc': ['application/msword'],
	'docx': ['application/vnd.openxmlformats-officedocument.wordprocessingml.document'],
	'odt': ['application/vnd.oasis.opendocument.text'],
	'ods': ['application/vnd.oasis.opendocument.spreadsheet'],
	'odp': ['application/vnd.oasis.opendocument.presentation'],
	'epub': ['application/epub+zip'],
	'xls': ['application/vnd.ms-excel'],
	'xlsx': ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'],
	'xlsm': ['application/vnd.ms-excel.sheet.macroenabled.12'],
	'xlsb': ['application/vnd.ms-excel.sheet.binary.macroenabled.12'],
	'ppt': ['application/vnd.ms-powerpoint'],
	'pptx': ['application/vnd.openxmlformats-officedocument.presentationml.presentation'],
	'png': ['image/png'],
	'jpg': ['image/jpeg'],
	'jpeg': ['image/jpeg'],
	'gif': ['image/gif'],
	'webp': ['image/webp'],
	'bmp': ['image/bmp'],
	'tif': ['image/tiff'],
	'tiff': ['image/tiff'],
	'heic': ['image/heic', 'image/heif'],
	'heif': ['image/heic', 'image/heif'],
	'zip': ['application/zip', 'application/x-zip-compressed'],
	'gz': ['application/gzip', 'application/x-gzip'],
	'tgz': ['application/gzip', 'application/x-gzip'],
	'tar': ['application/x-tar'],
	'bz2': ['application/x-bzip2'],
	'xz': ['application/x-xz'],
	'7z': ['application/x-7z-compressed'],
	'rar': ['application/vnd.rar', 'application/x-rar-compressed'],
	'eml': ['message/rfc822'],
	'msg': ['application/vnd.ms-outlook'],
	'ics': ['text/calendar'],
	'mp3': ['audio/mpeg'],
	'm4a': ['audio/mp4', 'audio/x-m4a'],
	'wav': ['audio/x-wav', 'audio/wav'],
	'mp4': ['video/mp4'],
	'mov': ['video/quicktime'],
	'm4v': ['video/x-m4v'],
	'pem': ['application/x-pem-file', 'text/plain'],
	'p12': ['application/x-pkcs12'],
	'pfx': ['application/x-pkcs12'],
	'cer': ['application/x-x509-ca-cert'],
	'crt': ['application/x-x509-ca-cert', 'text/plain'],
	'key': ['application/x-pem-file', 'text/plain'],
}

# Dangerous MIME types that are always rejected regardless of extension.
BLOCKED_MIME_TYPES = {
	'application/x-executable',
	'application/x-msdownload',
	'application/x-msdos-program',
	'application/x-dosexec',
	'application/x-sh',
	'application/x-shellscript',
	'application/x-bat',
	'application/x-msi',
	'application/x-msdos-program',
	'application/java-archive',
	'application/x-java-archive',
	'application/x-ms-shortcut',
	'application/vnd.microsoft.portable-executable',
}


def allowed_file(filename):
	"""Return True when the filename's extension is permitted."""
	if not filename or '.' not in filename:
		return False
	return filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def detect_mime_type_from_bytes(data):
	"""Detect the MIME type of an in-memory payload via libmagic."""
	try:
		import magic
		return magic.from_buffer(data, mime=True)
	except Exception as e:
		logger.warning(f"MIME detection failed for in-memory payload: {e}")
		return 'application/octet-stream'


def detect_mime_type(file_path):
	"""Detect the MIME type of a file on disk via libmagic."""
	try:
		import magic
		return magic.from_file(file_path, mime=True)
	except Exception as e:
		logger.warning(f"MIME detection failed for {file_path}: {e}")
		return 'application/octet-stream'


def _check(detected_mime, filename):
	"""Shared validation logic. Returns (is_valid, detected_mime, error)."""
	ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''

	if detected_mime in BLOCKED_MIME_TYPES:
		return False, detected_mime, f'File type {detected_mime} is not allowed'

	# If the type cannot be determined, allow it (octet-stream is generic).
	if detected_mime == 'application/octet-stream':
		return True, detected_mime, None

	expected_mimes = EXTENSION_MIME_MAP.get(ext, [])
	if expected_mimes:
		for expected in expected_mimes:
			if detected_mime == expected:
				return True, detected_mime, None
			# Accept any subtype of the expected major type.
			if detected_mime.startswith(expected.split('/')[0] + '/'):
				return True, detected_mime, None
		logger.warning(
			f"MIME mismatch: {filename} has extension .{ext} "
			f"(expected {expected_mimes}) but detected {detected_mime}"
		)
		return False, detected_mime, (
			f'File content ({detected_mime}) does not match extension (.{ext})'
		)

	# Extension not in the map - allow if not a blocked type.
	return True, detected_mime, None


def validate_bytes(data, filename):
	"""Validate an in-memory payload against its filename.

	Returns (is_valid, detected_mime, error_message).
	"""
	return _check(detect_mime_type_from_bytes(data), filename)


def validate_file_content(file_path, filename):
	"""Validate a file on disk against its filename.

	Returns (is_valid, detected_mime, error_message).
	"""
	return _check(detect_mime_type(file_path), filename)
