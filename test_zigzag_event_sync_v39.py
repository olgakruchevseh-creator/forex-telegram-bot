import zigzag_scanner


def test_fingerprint_ignores_mutable_context_and_unrelated_ltf():
    base = {"event":"СТРУКТУРА","side":1,"main_side":1,"early_key":"","tf":"H4",
            "sequences":{"H4":"HH → HL","H1":"LH → LL"},
            "extrema":{"H4":{"high":1.39310,"low":1.38229},"H1":{"high":1.39000,"low":1.38500}},
            "adx":20,"swing_profiles":{"H1":{"control_price":1.388}}}
    changed = dict(base)
    changed["adx"] = 44
    changed["sequences"] = {"H4":"HH → HL","H1":"HH → HL"}
    changed["extrema"] = {"H4":{"high":1.39310,"low":1.38229},"H1":{"high":1.39200,"low":1.38600}}
    changed["swing_profiles"] = {"H1":{"control_price":1.391}}
    assert zigzag_scanner._fingerprint(base) == zigzag_scanner._fingerprint(changed)


def test_fingerprint_changes_on_confirmed_structural_extremum():
    a = {"event":"СТРУКТУРА","side":1,"main_side":1,"early_key":"","tf":"H4",
         "sequences":{"H4":"HH → HL"},"extrema":{"H4":{"high":1.39310,"low":1.38229}}}
    b = {**a, "extrema":{"H4":{"high":1.39420,"low":1.38229}}}
    assert zigzag_scanner._fingerprint(a) != zigzag_scanner._fingerprint(b)
