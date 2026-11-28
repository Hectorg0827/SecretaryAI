"""Provider-free regression cases for the default ACL expansion gate."""
import unittest
from check_default_acl_expansion import compare, normalize

BASE = [{"owner":"postgres","schema":"public","object_type":"r",
         "grantee":"anon","privilege":"SELECT"}]


class DefaultAclGateTests(unittest.TestCase):
    def test_same_inventory_passes(self):
        self.assertEqual(compare(BASE, list(reversed(BASE))), ([], [], []))

    def test_expansion_fails_even_for_non_client_role(self):
        extra = dict(BASE[0], grantee="service_role")
        added, removed, exposed = compare(BASE, BASE + [extra])
        self.assertEqual(len(added), 1)
        self.assertFalse(removed)
        self.assertFalse(exposed)

    def test_public_and_authenticated_exposure(self):
        new = BASE + [dict(BASE[0], grantee="PUBLIC", privilege="EXECUTE"),
                      dict(BASE[0], grantee="authenticated", privilege="DELETE")]
        self.assertEqual(len(compare(BASE, new)[2]), 2)

    def test_removal_is_not_expansion(self):
        self.assertEqual(compare(BASE, []), ([], [("postgres","public","r","anon","SELECT")], []))

    def test_malformed_input_fails_closed(self):
        with self.assertRaises((ValueError, KeyError)):
            normalize([{"owner": "postgres"}])


if __name__ == "__main__":
    unittest.main()
