import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app
from db_helpers import get_db_connection


class QRReportsTests(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.application.config['TESTING'] = True

    def test_create_and_lookup_qr_report_record(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM qr_reports")
            conn.commit()

        with self.app.session_transaction() as sess:
            sess['user'] = 'admin'
            sess['user_id'] = 1
            sess['role'] = 'admin'

        response = self.app.post('/api/qr-report/download-history', json={
            'grn_number': 'GRN-1001',
            'item_name': 'Test Item',
            'item_code': 'IT-001',
            'supplier_name': 'ABC Corp',
            'quantity': 5,
            'report_filename': 'report_1.pdf',
            'report_filepath': '/tmp/report_1.pdf',
            'generated_at': '2026-07-28T10:00:00',
            'downloaded_at': '2026-07-28T10:05:00',
            'downloaded_by': 'admin'
        })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['record']['grn_number'], 'GRN-1001')

        response = self.app.get('/api/qr-report/download-history')
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['records'][0]['grn_number'], 'GRN-1001')

    def test_archives_printed_qr_report_html(self):
        with get_db_connection() as conn:
            conn.execute("DELETE FROM qr_reports")
            conn.commit()

        with self.app.session_transaction() as sess:
            sess['user'] = 'admin'
            sess['user_id'] = 1
            sess['role'] = 'admin'

        report_html = '<html><body><h1>Printed QR Report</h1></body></html>'
        response = self.app.post('/api/qr-report/download-history', json={
            'grn_number': 'GRN-2001',
            'item_name': 'Printed Item',
            'item_code': 'IT-200',
            'supplier_name': 'XYZ Ltd',
            'quantity': 3,
            'report_html': report_html,
            'generated_at': '2026-07-28T11:00:00',
            'downloaded_at': '2026-07-28T11:05:00',
            'downloaded_by': 'admin'
        })

        self.assertEqual(response.status_code, 200)
        record = response.get_json()['record']
        self.assertEqual(record['grn_number'], 'GRN-2001')

        file_path = os.path.join(app.root_path, 'static', 'qr_reports', record['report_filename'])
        self.assertTrue(os.path.exists(file_path))
        with open(file_path, 'r', encoding='utf-8') as handle:
            self.assertIn('Printed QR Report', handle.read())


if __name__ == '__main__':
    unittest.main()
