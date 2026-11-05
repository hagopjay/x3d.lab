"""The fuzzer: grammar, generator, mutators, oracles, bucketing and the minimiser. Offline tests use the X3DUOM and
XSD from the pinned x3d_mcp checkout (X3DLAB_X3D_MCP_DIR); the last ones use the live server (`lab` fixture)."""

import asyncio
import random

import pytest

from x3dlab import fuzz, uom


@pytest.fixture(scope="module")
def u():
    return uom.load()


@pytest.fixture(scope="module")
def xsd():
    return fuzz.XSDOracle()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------- the grammar
def test_uom_knows_nodes_fields_and_placement(u):
    assert len(u.nodes) > 250
    t = u.nodes["Transform"]
    assert "X3DGroupingNode" in t.ancestors and t.fields["translation"].type == "SFVec3f"
    geo = u.accepts(u.nodes["Shape"].fields["geometry"].accepts)
    assert {"Box", "Sphere", "IndexedFaceSet"} <= set(geo) and "Material" not in geo
    cone = u.nodes["Cone"].fields["bottomRadius"]
    assert cone.min == 0 and cone.min_x                     # minExclusive
    assert not u.nodes["TimeSensor"].fields["fraction_changed"].settable
    assert "Box" in uom.profile_nodes("Interchange") and "Fog" not in uom.profile_nodes("Interchange")


def test_values_respect_type_range_and_enum(u):
    rnd = random.Random(1)
    for _ in range(200):
        v = fuzz.value_for(rnd, u.nodes["Material"].fields["transparency"])
        assert 0 <= float(v) <= 1
        assert len(fuzz.value_for(rnd, u.nodes["Transform"].fields["rotation"]).split()) == 4
        assert fuzz.value_for(rnd, u.nodes["Fog"].fields["fogType"]) in ("LINEAR", "EXPONENTIAL")


# ---------------------------------------------------------------------- generation
def test_cases_are_deterministic_and_cover_operators(u):
    cfg = fuzz.FuzzConfig(n=120, seed=3)
    a, b = fuzz.make_cases(cfg, u), fuzz.make_cases(cfg, u)
    assert [c.xml for c in a] == [c.xml for c in b]
    ops = {c.op for c in a}
    assert "clean" in ops and len(ops & set(fuzz.Mutators.REJECT)) >= 12 and len(ops & set(fuzz.Mutators.ACCEPT)) >= 4
    assert all(c.expect == ("accept" if c.op == "clean" or c.op in fuzz.Mutators.ACCEPT else "reject") for c in a)


def test_generated_scenes_pass_the_xsd(u, xsd):
    gen = fuzz.Gen(u, random.Random(5))
    bad = []
    for _ in range(40):
        o = run(xsd(fuzz.to_xml(gen.scene())))
        if o.verdict != "accept":
            bad.append(o.detail)
    assert not bad, bad[:3]


def test_use_always_follows_its_def(u):
    gen = fuzz.Gen(u, random.Random(9))
    for _ in range(60):
        root = gen.scene()
        seen = set()
        for e in root.iter():
            if e.get("USE"):
                assert e.get("USE") in seen
            if e.get("DEF"):
                seen.add(e.get("DEF"))


@pytest.mark.parametrize("op", ["enum", "arity", "not_a_number", "unknown_attr", "unknown_node", "wrong_child", "dup_def",
                                "event_attr", "bad_version", "truncate"])
def test_schema_level_mutations_are_caught_by_the_xsd(u, xsd, op):
    cases = [c for c in fuzz.make_cases(fuzz.FuzzConfig(n=400, seed=11, ops=[op], clean=0, metamorphic=0), u)][:5]
    assert cases, op
    for c in cases:
        assert run(xsd(c.xml)).verdict == "reject", (op, c.note)


def test_metamorphic_rewrites_keep_documents_valid(u, xsd):
    ops = ["reorder_attrs", "comments", "explicit_defaults", "wrap_group", "rename_defs", "whitespace"]
    for c in fuzz.make_cases(fuzz.FuzzConfig(n=60, seed=4, ops=ops, clean=0, metamorphic=1.0), u):
        assert run(xsd(c.xml)).verdict == "accept", (c.op, c.note)


def test_the_xsd_rejects_in_range_colours_written_with_an_exponent(xsd):
    """A finding of this fuzzer: SFColor's XSD pattern checks the mantissa, so 3.794e-01 (= 0.3794) is refused."""
    doc = '<X3D profile="Full" version="4.0"><Scene><Shape><Appearance><Material diffuseColor="%s"/></Appearance></Shape></Scene></X3D>'
    assert run(xsd(doc % "0.5 0.3794 0.2")).verdict == "accept"
    assert run(xsd(doc % "0.5 3.794e-01 0.2")).verdict == "reject"


# ---------------------------------------------------------------------- signatures, buckets, scores
def test_norm_and_fine_strip_specifics_but_fine_keeps_names():
    m = "Line 12: Element 'Material', attribute 'diffuseColor': [facet 'pattern'] The value '0.4 3e-1 0.2' is not accepted"
    assert "12" not in fuzz.norm(m) and "Material" not in fuzz.norm(m)
    f = fuzz.fine(m)
    assert "'Material'" in f and "'diffuseColor'" in f and "0.4" not in f


class Fixed(fuzz.Oracle):
    def __init__(self, name, fn):
        self.name, self.fn = name, fn

    async def __call__(self, xml):
        return self.fn(xml)


def test_confusion_agreement_and_buckets():
    cases = [fuzz.Case("a", "<X3D bad/>", "unknown_attr", "reject", target="X3D"),
             fuzz.Case("b", "<X3D/>", "clean", "accept"),
             fuzz.Case("c", "<X3D crash/>", "truncate", "reject", target="document")]
    strict = Fixed("strict", lambda x: fuzz.Outcome("strict", "reject" if "bad" in x or "crash" in x else "accept", "sig-bad"))
    lenient = Fixed("lenient", lambda x: fuzz.Outcome("lenient", "crash" if "crash" in x else "accept", "boom"))
    fr = fuzz.run(fuzz.FuzzConfig(minimize=False), [strict, lenient], cases=cases)
    conf = fr.confusion()
    assert conf["strict"]["recall"] == 1.0 and conf["strict"]["specificity"] == 1.0
    assert conf["lenient"]["missed"] == 1 and conf["lenient"]["crash"] == 1
    kinds = {b.kind for b in fr.buckets.values()}
    assert kinds == {"missed", "crash"}
    assert fr.agreement()["strict~lenient"]["n"] == 2
    card = fr.card()
    assert card["verdicts"]["a"] == {"strict": "reject", "lenient": "accept"} and card["expect"]["b"] == "accept"


# ---------------------------------------------------------------------- minimisation
def test_ddmin_finds_the_one_needed_item():
    calls = []

    async def test(keep):
        calls.append(1)
        return 7 in keep

    assert run(fuzz.ddmin(list(range(16)), test, [100])) == [7]
    assert run(fuzz.ddmin([1, 2], lambda keep: _true(), [10])) == []
    assert len(calls) < 40


def test_hdd_shrinks_to_the_culprit_and_keeps_the_site(u, xsd):
    gen = fuzz.Gen(u, random.Random(2))
    root = gen.scene(depth=3, groups=3)
    mats = list(root.iter("Material"))
    target = mats[-1]
    target.set("diffuseColor", "0.5 3.794e-01 0.2")            # the exponent quirk: rejected by the XSD
    site = fuzz.path_of(root, target)
    xml = fuzz.to_xml(root)

    async def still(x):
        o = await xsd(x)
        return o.verdict == "reject" and "diffuseColor" in o.detail

    small, used = run(fuzz.hdd(xml, still, 120, site, "diffuseColor"))
    assert len(small) < len(xml) / 2 and "3.794e-01" in small and used <= 120
    assert run(still(small))


def test_hdd_keeps_what_a_use_needs(u):
    xml = ('<X3D profile="Full" version="4.0"><head/><Scene><Group><Shape DEF="A"><Box/></Shape></Group>'
           '<Transform><Shape USE="A" bboxSize="1 1 1"/></Transform><Group><Shape><Sphere/></Shape></Group></Scene></X3D>')
    root = fuzz.parse(xml)
    use = [e for e in root.iter("Shape") if e.get("USE")][0]

    async def always(x):
        return True                                            # "nobody caught it": nothing can notice the defect leaving

    small, _ = run(fuzz.hdd(xml, always, 80, fuzz.path_of(root, use), "bboxSize"))
    assert 'USE="A"' in small and 'bboxSize="1 1 1"' in small and 'DEF="A"' in small and "Sphere" not in small


# ---------------------------------------------------------------------- with the live server
def test_fuzz_against_the_live_validator(lab, xsd):
    cfg = fuzz.FuzzConfig(n=30, seed=21, min_budget=20)
    fr = fuzz.run(cfg, [fuzz.MCPOracle(lab.client, "fork"), xsd])
    conf = fr.confusion()
    assert conf["fork"]["recall"] >= 0.8 and conf["fork"].get("crash", 0) == 0
    for b in fr.buckets.values():
        if b.repro:
            assert len(b.repro) <= b.orig_bytes


async def _true():
    return True
