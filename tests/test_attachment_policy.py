"""Tests for the shared attachment policy.

The upload endpoint and the raw MIME endpoint must accept and reject the
same files, and both must expose the policy so a client can build its file
picker from it instead of keeping a second, drifting copy.
"""

import io
import zipfile

import pytest


def _xlsx_bytes():
	"""A minimal but genuine .xlsx container."""
	buf = io.BytesIO()
	with zipfile.ZipFile(buf, 'w') as zf:
		zf.writestr('[Content_Types].xml',
			'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
			'<Default Extension="xml" ContentType="application/xml"/>'
			'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
			'</Types>')
		zf.writestr('xl/workbook.xml',
			'<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>')
	return buf.getvalue()


def _pdf_bytes():
	"""A minimal but genuine PDF document."""
	return (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
	        b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
	        b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n'
	        b'xref\n0 4\ntrailer<</Size 4/Root 1 0 R>>\n%%EOF\n')


class TestAttachmentPolicy:

	def test_spreadsheet_upload_is_accepted(self, client, auth_headers, db):
		"""Spreadsheets are ordinary business attachments and must work."""
		email_id = client.post('/api/emails', headers=auth_headers, json={
			'to': 'recipient@example.com',
			'subject': 'With a spreadsheet',
			'body': 'See attached',
		}).get_json()['id']

		response = client.post(
			f'/api/emails/{email_id}/attachments',
			headers=auth_headers,
			data={'file': (io.BytesIO(_xlsx_bytes()), 'sheet.xlsx')},
			content_type='multipart/form-data',
		)
		assert response.status_code == 201, response.get_json()

	def test_csv_upload_is_accepted(self, client, auth_headers, db):
		email_id = client.post('/api/emails', headers=auth_headers, json={
			'to': 'recipient@example.com',
			'subject': 'With a csv',
			'body': 'See attached',
		}).get_json()['id']

		response = client.post(
			f'/api/emails/{email_id}/attachments',
			headers=auth_headers,
			data={'file': (io.BytesIO(b'a,b,c\n1,2,3\n'), 'data.csv')},
			content_type='multipart/form-data',
		)
		assert response.status_code == 201, response.get_json()

	def test_executable_upload_is_rejected(self, client, auth_headers, db):
		"""Executables stay blocked whatever the extension says."""
		email_id = client.post('/api/emails', headers=auth_headers, json={
			'to': 'recipient@example.com',
			'subject': 'With a bad file',
			'body': 'See attached',
		}).get_json()['id']

		response = client.post(
			f'/api/emails/{email_id}/attachments',
			headers=auth_headers,
			data={'file': (io.BytesIO(b'MZ\x90\x00binary'), 'payload.exe')},
			content_type='multipart/form-data',
		)
		assert response.status_code == 400
		assert 'not allowed' in response.get_json()['error']

	def test_content_mismatch_is_rejected(self, client, auth_headers, db):
		"""A PDF renamed to .txt is still a mismatch."""
		email_id = client.post('/api/emails', headers=auth_headers, json={
			'to': 'recipient@example.com',
			'subject': 'With a mismatched file',
			'body': 'See attached',
		}).get_json()['id']

		response = client.post(
			f'/api/emails/{email_id}/attachments',
			headers=auth_headers,
			data={'file': (io.BytesIO(_pdf_bytes()), 'notes.txt')},
			content_type='multipart/form-data',
		)
		assert response.status_code == 400
		assert 'does not match extension' in response.get_json()['error']

	def test_allowed_types_endpoint_lists_the_policy(self, client, auth_headers, db):
		"""Clients build their file picker from this, so it must be complete."""
		response = client.get('/api/attachments/allowed-types', headers=auth_headers)
		assert response.status_code == 200
		data = response.get_json()

		extensions = data['allowed_extensions']
		for expected in ('pdf', 'csv', 'xlsx', 'docx', 'png', 'zip', 'txt'):
			assert expected in extensions
		for forbidden in ('exe', 'sh', 'bat', 'msi'):
			assert forbidden not in extensions
		assert data['max_file_size'] == 10 * 1024 * 1024
