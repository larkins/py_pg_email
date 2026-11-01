"""Regression tests for duplicate attachment delivery.

The raw MIME send path stores the attachment parts inside raw_email *and*
records the same files in the attachments table. The delivery step used to
re-attach every row in that table without checking the message it was
building, so each file went out twice.

The fix matches on content digest, so the step is idempotent while still
allowing a sender to deliberately attach the same file more than once.
"""

import os
import tempfile

from email.message import EmailMessage
from email import policy
from email.parser import BytesParser

from smtp_server.outbound.queue_processor import attach_attachments


def _attachment_parts(msg):
	"""Return the parts of a message that carry a filename."""
	return [p for p in msg.walk() if p.get_filename()]


def _rows(tmpdir, specs):
	"""Build attachment-table rows backed by real files on disk."""
	rows = []
	for filename, data in specs:
		path = os.path.join(tmpdir, filename)
		with open(path, 'wb') as f:
			f.write(data)
		rows.append({
			'file_path': path,
			'file_name': filename,
			'content_type': 'text/plain',
		})
	return rows


def _message_with_attachment(payload=b'file contents', filename='note.txt'):
	msg = EmailMessage()
	msg.set_content('please see attached')
	msg.add_attachment(payload, maintype='text', subtype='plain', filename=filename)
	return msg


class TestAttachAttachments:

	def test_attachment_already_embedded_is_not_added_twice(self):
		"""The MIME send path embeds the part *and* records a table row."""
		with tempfile.TemporaryDirectory() as tmpdir:
			rows = _rows(tmpdir, [('note.txt', b'file contents')])
			msg = _message_with_attachment(b'file contents', 'note.txt')

			attached, skipped = attach_attachments(msg, rows)

			assert len(_attachment_parts(msg)) == 1
			assert attached == []
			assert skipped == ['note.txt']

	def test_new_attachment_is_added_once(self):
		"""The JSON send path has no embedded part, so it is attached."""
		with tempfile.TemporaryDirectory() as tmpdir:
			rows = _rows(tmpdir, [('note.txt', b'file contents')])
			msg = EmailMessage()
			msg.set_content('please see attached')

			attached, skipped = attach_attachments(msg, rows)

			assert len(_attachment_parts(msg)) == 1
			assert attached == ['note.txt']
			assert skipped == []

	def test_several_distinct_files_are_all_attached(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			rows = _rows(tmpdir, [
				('one.txt', b'first file'),
				('two.csv', b'second file'),
			])
			msg = EmailMessage()
			msg.set_content('please see attached')

			attached, skipped = attach_attachments(msg, rows)

			assert len(_attachment_parts(msg)) == 2
			assert attached == ['one.txt', 'two.csv']
			assert skipped == []

	def test_same_file_attached_twice_deliberately_is_kept(self):
		"""Identical content twice with nothing embedded means two parts."""
		with tempfile.TemporaryDirectory() as tmpdir:
			rows = _rows(tmpdir, [
				('one.txt', b'same file'),
				('two.txt', b'same file'),
			])
			msg = EmailMessage()
			msg.set_content('please see attached')

			attached, skipped = attach_attachments(msg, rows)

			assert len(_attachment_parts(msg)) == 2
			assert attached == ['one.txt', 'two.txt']
			assert skipped == []

	def test_missing_file_is_skipped_without_raising(self):
		with tempfile.TemporaryDirectory() as tmpdir:
			rows = [{
				'file_path': os.path.join(tmpdir, 'gone.txt'),
				'file_name': 'gone.txt',
				'content_type': 'text/plain',
			}]
			msg = EmailMessage()
			msg.set_content('please see attached')

			attached, skipped = attach_attachments(msg, rows)

			assert attached == []
			assert skipped == []
			assert len(_attachment_parts(msg)) == 0

	def test_stored_raw_email_round_trip_stays_single_copy(self):
		"""The real path: raw_email is parsed, then the table is re-applied."""
		with tempfile.TemporaryDirectory() as tmpdir:
			rows = _rows(tmpdir, [('note.txt', b'file contents')])
			msg = _message_with_attachment(b'file contents', 'note.txt')

			# What the queue processor does with the stored raw_email.
			parsed = BytesParser(policy=policy.default).parsebytes(
				msg.as_bytes(policy=policy.default)
			)

			attach_attachments(parsed, rows)

			parts = _attachment_parts(parsed)
			assert len(parts) == 1
			assert parts[0].get_filename() == 'note.txt'
			assert parts[0].get_payload(decode=True) == b'file contents'
