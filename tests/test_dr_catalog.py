from mqlab.dr.catalog import CATALOG, Kind, Scenario


def test_catalog_has_every_spec_scenario():
    ids = {s.id for s in CATALOG}
    assert ids == {
        "HA-1",
        "HA-2",
        "HA-3",
        "HA-4",
        "HA-5",
        "DR-CTRL",
        "DR-FORCE-1",
        "DR-FORCE-2",
        "DR-FORCE-3",
        "FB-REPLAY",
    }


def test_ha_scenarios_expect_rpo_zero():
    for s in CATALOG:
        if s.kind is Kind.HA:
            assert s.expect_rpo_zero is True


def test_forced_dr_scenarios_expect_loss():
    forced = [s for s in CATALOG if s.id.startswith("DR-FORCE")]
    assert forced and all(s.expect_rpo_zero is False for s in forced)


def test_controlled_dr_expects_rpo_zero():
    ctrl = next(s for s in CATALOG if s.id == "DR-CTRL")
    assert ctrl.expect_rpo_zero is True


def test_every_scenario_names_its_fault_and_expected_buckets():
    for s in CATALOG:
        assert s.fault  # non-empty description of what is injected
        assert s.expect_buckets  # at least one bucket it should light up


def test_scenario_is_constructible():
    s = Scenario("X", Kind.HA, "fault", True, ())
    assert s.id == "X"
