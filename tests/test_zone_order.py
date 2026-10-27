import os, re, unittest

SRC = open(os.path.join(os.path.dirname(__file__), "..", "src", "omen-centre.py")).read()


def load(raw):
    m = re.search(r"def load_zone_order\(\):.*?\n\n\n", SRC, re.S)
    ns = {"os": os, "load_pref": lambda k: raw}
    os.environ.pop("OMEN_ZONE_ORDER", None)
    exec(m.group(0), ns)
    return ns["load_zone_order"]()


class ZoneOrder(unittest.TestCase):
    def test_default_identity(self):
        self.assertEqual(load(""), [0, 1, 2, 3])

    def test_reversed(self):
        self.assertEqual(load("3,2,1,0"), [3, 2, 1, 0])

    def test_rejects_non_permutation(self):
        for bad in ("0,0,1,2", "1,2,3", "a,b,c,d", "0,1,2,4"):
            self.assertEqual(load(bad), [0, 1, 2, 3])


if __name__ == "__main__":
    unittest.main()
