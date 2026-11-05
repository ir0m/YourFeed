from yourfeed.pipeline.select import allocate, by_site, fill_by_weight, interleave, matches


def test_allocate_uses_largest_remainder_and_keeps_order_on_ties():
    assert allocate({"a": 15, "b": 15, "c": 2, "d": 2}, 34) == {"a": 15, "b": 15, "c": 2, "d": 2}
    assert allocate({"a": 1, "b": 1, "c": 1}, 2) == {"a": 1, "b": 1, "c": 0}
    assert allocate({"a": 0, "b": 0}, 5) == {"a": 0, "b": 0}
    assert sum(allocate({"a": 3, "b": 7, "c": 11}, 50).values()) == 50


def test_terms_match_words_in_english_and_substrings_in_japanese():
    assert matches("New AI model released", ["ai"])
    assert not matches("Said the chair", ["ai"])
    assert matches("量子コンピュータの新しい成果", ["量子"])
    assert matches("ＧＤＰ速報", ["gdp"])
    assert not matches("anything", ["", "  "])


def test_fill_by_weight_moves_a_short_share_and_widens_the_window():
    available = {"a": {"new": 2, "old": 5}, "b": {"new": 10, "old": 0}, "c": {"new": 0, "old": 0}}
    taken = {"a": 0, "b": 0, "c": 0}
    windows_used = []

    def fill(key, target, window):
        windows_used.append((key, window))
        while taken[key] < target and available[key][window]:
            available[key][window] -= 1
            taken[key] += 1
        return taken[key] >= target

    targets = fill_by_weight({"a": 3, "b": 1, "c": 2, "off": 0}, 12, ["new", "old"], taken.get, fill)
    assert sum(taken.values()) == 12
    # c finds nothing and a finds 2 of 6 new ones, so b takes the rest of the new window.
    assert taken == {"a": 2, "b": 10, "c": 0}
    assert targets["off"] == 0 and ("off", "new") not in windows_used
    assert ("a", "old") not in windows_used  # the count was filled before the wider window


def test_fill_by_weight_uses_the_wider_window_when_the_first_runs_out():
    available = {"a": {"new": 1, "old": 5}}
    taken = {"a": 0}

    def fill(key, target, window):
        while taken[key] < target and available[key][window]:
            available[key][window] -= 1
            taken[key] += 1
        return taken[key] >= target

    fill_by_weight({"a": 1}, 4, ["new", "old"], taken.get, fill)
    assert taken == {"a": 4} and available["a"] == {"new": 0, "old": 2}


def test_by_site_alternates_sites_in_order():
    items = [("a1", "https://a.example/1"), ("a2", "https://www.a.example/2"), ("a3", "https://a.example/3"),
             ("b1", "https://b.example/1")]
    assert by_site(items) == ["a1", "b1", "a2", "a3"]


def test_interleave_spreads_groups():
    assert interleave([["a1", "a2", "a3", "a4"], ["b1", "b2"]]) == ["a1", "b1", "a2", "a3", "b2", "a4"]
    assert interleave([]) == []
