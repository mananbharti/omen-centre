import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import light_engine as le


class LayerTest(unittest.TestCase):
    def test_reject_unknown_type(self):
        with self.assertRaises(ValueError):
            le.Layer("x", ltype="nope")

    def test_reject_bad_zone_mask(self):
        with self.assertRaises(ValueError):
            le.Layer("x", zones=(0, 4))
        with self.assertRaises(ValueError):
            le.Layer("x", zones=())

    def test_speed_clamped(self):
        l = le.Layer("x", speed=500)
        self.assertEqual(l.speed, 100)

    def test_static_returns_primary(self):
        l = le.Layer("x", ltype="static", primary="112233")
        self.assertEqual(l.color_at(0, 0.0), (0x11, 0x22, 0x33))
        self.assertEqual(l.color_at(2, 9.7), (0x11, 0x22, 0x33))

    def test_breathing_bounds(self):
        l = le.Layer("x", ltype="breathing", primary="FF0000")
        for t in (0.0, 0.13, 0.5, 0.77, 1.0, 3.3):
            r, g, b = l.color_at(0, t)
            self.assertTrue(0 <= r <= 255)
            self.assertEqual((g, b), (0, 0))

    def test_gradient_interpolates_across_zones(self):
        l = le.Layer("x", ltype="gradient", primary="000000",
                     secondary="FFFFFF", zones=(0, 1, 2, 3))
        c0 = l.color_at(0, 0)
        c3 = l.color_at(3, 0)
        self.assertEqual(c0, (0, 0, 0))
        self.assertEqual(c3, (255, 255, 255))
        self.assertLess(c0[0], l.color_at(1, 0)[0])


class CompositorTest(unittest.TestCase):
    def test_base_passthrough(self):
        c = le.Compositor(["FF0000", "00FF00", "0000FF", "FFFFFF"])
        self.assertEqual(c.tick(0.0),
                         ["FF0000", "00FF00", "0000FF", "FFFFFF"])

    def test_top_layer_wins_only_on_its_zones(self):
        c = le.Compositor(["101010"] * 4)
        c.add_layer(le.Layer("a", ltype="static", primary="FF0000",
                             zones=(1,)))
        out = c.tick(0.0)
        self.assertEqual(out, ["101010", "FF0000", "101010", "101010"])

    def test_stacked_order(self):
        c = le.Compositor()
        c.add_layer(le.Layer("bottom", ltype="static", primary="00FF00",
                             zones=(0, 1, 2, 3)))
        c.add_layer(le.Layer("top", ltype="static", primary="FF0000",
                             zones=(2,)))
        out = c.tick(0.0)
        self.assertEqual(out[2], "FF0000")
        self.assertEqual(out[0], "00FF00")

    def test_disabled_layer_skipped(self):
        c = le.Compositor()
        l = c.add_layer(le.Layer("a", ltype="static", primary="FF0000"))
        l.enabled = False
        self.assertEqual(c.tick(0.0)[0], "FFFFFF")

    def test_animated_detection(self):
        c = le.Compositor()
        self.assertFalse(c.animated())
        c.add_layer(le.Layer("a", ltype="static"))
        self.assertFalse(c.animated())
        c.add_layer(le.Layer("b", ltype="wave"))
        self.assertTrue(c.animated())

    def test_wave_varies_over_time(self):
        c = le.Compositor()
        c.add_layer(le.Layer("w", ltype="wave", primary="FF8800"))
        a = c.tick(0.0)
        b = c.tick(0.4)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()


class LayerOpsTest(unittest.TestCase):
    def test_secondary_only_for_types_that_use_it(self):
        import light_engine as le
        for t in le.LAYER_TYPES:
            self.assertEqual(le.Layer("x", t).uses_secondary(),
                             t in ("wave", "gradient"))

    def test_clone_is_independent(self):
        import light_engine as le
        a = le.Layer("a", "gradient", zones=(0, 1))
        b = a.clone("b")
        b.primary = "123456"
        b.zones = (3,)
        self.assertEqual((a.name, a.primary, a.zones),
                         ("a", "FF8800", (0, 1)))
