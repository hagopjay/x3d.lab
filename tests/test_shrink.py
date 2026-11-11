"""The shrinker: smallest scene that keeps a behaviour, valid throughout, within budget."""

from x3dlab import shrink

SCENE = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Immersive" version="4.0"><head><meta name="title" content="t"/></head><Scene>
  <Viewpoint position="0 0 10" description="front"/>
  <Background skyColor="0 0 0.2"/>
  <Transform translation="1 2 3" rotation="0 1 0 0.5">
    <Shape DEF="Body"><Appearance><Material diffuseColor="1 0 0" shininess="0.5"/></Appearance><Box size="1 2 3"/></Shape>
    <Transform translation="4 0 0"><Shape USE="Body"/></Transform>
  </Transform>
  <Shape><Appearance><Material diffuseColor="0 1 0"/></Appearance>
    <IndexedFaceSet coordIndex="0 1 2 -1 0 2 3 -1"><Coordinate point="0 0 0 1 0 0 1 1 0 0 1 0 2 2 2 3 3 3 4 4 4 5 5 5"/></IndexedFaceSet></Shape>
  <TimeSensor DEF="T" cycleInterval="4" loop="true"/>
</Scene></X3D>"""


def no_dangling_use(xml):
    defs = set(__import__("re").findall(r'DEF="([^"]+)"', xml))
    return all(u in defs for u in __import__("re").findall(r'USE="([^"]+)"', xml))


def test_keeps_only_what_the_behaviour_needs():
    keep = lambda x: "<Box" in x                                   # noqa: E731  the behaviour: "there is a Box"
    r = shrink.shrink(SCENE, keep, valid=no_dangling_use, budget=200)
    assert "<Box" in r.xml and "<Background" not in r.xml and "IndexedFaceSet" not in r.xml and "TimeSensor" not in r.xml
    assert 'size="1 2 3"' not in r.xml and "shininess" not in r.xml          # attributes go too
    assert r.after["nodes"] < r.before["nodes"] and r.ratio < 0.5 and not r.exhausted
    assert no_dangling_use(r.xml) and r.xml.startswith("<?xml") and 'profile="Immersive"' in r.xml


def test_valid_guard_protects_def_use():
    keep = lambda x: 'USE="Body"' in x                             # noqa: E731
    r = shrink.shrink(SCENE, keep, valid=no_dangling_use, budget=200)
    assert 'DEF="Body"' in r.xml and 'USE="Body"' in r.xml        # the DEF can never go while its USE is needed


def test_halves_long_lists_by_whole_tuples():
    keep = lambda x: "<Coordinate point=" in x and "IndexedFaceSet" in x  # noqa: E731
    r = shrink.shrink(SCENE, keep, budget=200)
    pts = __import__("re").search(r'point="([^"]*)"', r.xml).group(1).split()
    assert len(pts) % 3 == 0 and len(pts) < 24


def test_budget_is_respected():
    calls = []
    r = shrink.shrink(SCENE, lambda x: calls.append(1) or True, budget=5)
    assert r.checks == 5 and len(calls) == 5 and r.exhausted


def test_a_keep_that_throws_is_a_no():
    def boom(x):
        raise RuntimeError("engine died")
    r = shrink.shrink(SCENE, boom, budget=10)
    assert r.steps == [] and r.after == r.before


def test_unwraps_groups_and_drops_meta():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Immersive" version="4.0"><head><meta name="title" content="t"/></head><Scene>
  <Group><Transform translation="1 0 0"><Shape><Box/></Shape></Transform></Group>
</Scene></X3D>"""
    keep = lambda x: "<Box" in x                                   # noqa: E731
    r = shrink.shrink(xml, keep, budget=50)
    assert "<Group" not in r.xml and "<Transform" not in r.xml and "<meta" not in r.xml and "<Box" in r.xml
    assert any(s.startswith("unwrap") for s in r.steps)
