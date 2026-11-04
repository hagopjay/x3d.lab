"""Each Break-it defect is caught by the layer the catalog says. If a validator change moves one, this fails:
a tiny regression test on the pinned x3d_mcp."""

import pytest

from x3dlab import breakit


@pytest.mark.parametrize("defect", [d.id for d in breakit.CATALOG])
def test_defect_caught_where_expected(lab, defect):
    o = lab.breakit(defect)
    assert o.caught_by == o.expect, "%s: caught by %s, expected %s (%s)" % (defect, o.caught_by, o.expect, o.first_error)


def test_exact_fixes_repair_profile_and_container(lab):
    for d in ("pbr_no_component", "wrong_container", "missing_profile"):
        assert lab.breakit(d).repaired is True
