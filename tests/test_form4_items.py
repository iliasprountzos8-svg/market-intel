import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
import form4_items as fi  # noqa: E402

SUBMISSIONS_FIXTURE = {
    "filings": {
        "recent": {
            "form": ["8-K", "4", "4/A", "10-Q"],
            "accessionNumber": ["0001-8k", "0002-form4", "0003-form4a", "0004-10q"],
            "filingDate": ["2026-09-20", "2026-09-21", "2026-09-15", "2026-09-10"],
            "acceptanceDateTime": ["2026-09-20T16:00:00.000Z", "2026-09-21T20:05:00.000Z",
                                    "2026-09-15T18:00:00.000Z", "2026-09-10T12:00:00.000Z"],
            "primaryDocument": ["8k.htm", "xslF345X06/wk-form4_1.xml", "xslF345X06/wk-form4_2.xml", "10q.htm"],
        }
    }
}

FORM4_XML_FIXTURE = """<?xml version="1.0"?>
<ownershipDocument>
    <issuer>
        <issuerCik>0001045810</issuerCik>
        <issuerName>NVIDIA CORP</issuerName>
        <issuerTradingSymbol>NVDA</issuerTradingSymbol>
    </issuer>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerCik>0001696841</rptOwnerCik>
            <rptOwnerName>Teter Timothy S.</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>0</isDirector>
            <isOfficer>1</isOfficer>
            <isTenPercentOwner>0</isTenPercentOwner>
            <officerTitle>EVP, General Counsel and Sec</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionDate><value>2026-09-21</value></transactionDate>
            <transactionCoding>
                <transactionCode>S</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares><value>12483</value></transactionShares>
                <transactionPricePerShare><value>222.1932</value></transactionPricePerShare>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>2705637</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>
"""

FORM4_XML_NO_NONDERIV = """<?xml version="1.0"?>
<ownershipDocument>
    <issuer><issuerTradingSymbol>NVDA</issuerTradingSymbol></issuer>
    <reportingOwner>
        <reportingOwnerId><rptOwnerName>Some Director</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship><isDirector>1</isDirector><isOfficer>0</isOfficer><isTenPercentOwner>0</isTenPercentOwner></reportingOwnerRelationship>
    </reportingOwner>
    <derivativeTable><derivativeTransaction></derivativeTransaction></derivativeTable>
</ownershipDocument>
"""


class ListForm4Filings(unittest.TestCase):
    def test_filters_to_form4_only(self):
        out = fi.list_form4_filings(SUBMISSIONS_FIXTURE)
        accessions = {f["accession"] for f in out}
        self.assertEqual(accessions, {"0002-form4", "0003-form4a"})

    def test_strips_viewer_path_prefix_from_primary_document(self):
        out = fi.list_form4_filings(SUBMISSIONS_FIXTURE)
        docs = {f["primary_document"] for f in out}
        self.assertEqual(docs, {"wk-form4_1.xml", "wk-form4_2.xml"})

    def test_since_filter(self):
        out = fi.list_form4_filings(SUBMISSIONS_FIXTURE, since_iso="2026-09-20T00:00:00.000Z")
        self.assertEqual({f["accession"] for f in out}, {"0002-form4"})

    def test_empty_submissions(self):
        self.assertEqual(fi.list_form4_filings({}), [])
        self.assertEqual(fi.list_form4_filings(None), [])


class ParseForm4Xml(unittest.TestCase):
    def test_extracts_transaction_fields(self):
        txns = fi.parse_form4_xml(FORM4_XML_FIXTURE, "NVDA", "0001045810", "0002-form4", "2026-09-21T20:05:00+00:00")
        self.assertEqual(len(txns), 1)
        t = txns[0]
        self.assertEqual(t["symbol"], "NVDA")
        self.assertEqual(t["insider_name"], "Teter Timothy S.")
        self.assertTrue(t["is_officer"])
        self.assertFalse(t["is_director"])
        self.assertEqual(t["officer_title"], "EVP, General Counsel and Sec")
        self.assertEqual(t["transaction_code"], "S")
        self.assertEqual(t["transaction_date"], "2026-09-21")
        self.assertEqual(t["shares"], 12483.0)
        self.assertAlmostEqual(t["price_per_share"], 222.1932)
        self.assertEqual(t["shares_owned_after"], 2705637.0)

    def test_no_nonderivative_transactions_returns_empty(self):
        txns = fi.parse_form4_xml(FORM4_XML_NO_NONDERIV, "NVDA", "0001045810", "0003-form4a", "2026-09-15T18:00:00+00:00")
        self.assertEqual(txns, [])


if __name__ == "__main__":
    unittest.main()
