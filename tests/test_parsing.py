from x3dlab.mcpclient import combine, parse_semantic, parse_xsd

REPORT = """# Semantic Check Report

Found 2 error(s), 1 warning(s), 1 info(s).

## Errors

- **[route-type-mismatch]** ROUTE type mismatch: T.fraction_changed is SFFloat but X.set_translation is SFVec3f.
- **[route-before-def]** ROUTE fromNode='Turn' comes before DEF='Turn' (OrientationInterpolator at line 42).

## Warnings

- **[empty-group]** Transform (DEF='X') has no children.

## Info

- **[no-viewpoint]** Scene has no Viewpoint node.
"""


def test_semantic_report():
    issues = parse_semantic(REPORT)
    assert [(i.severity, i.rule) for i in issues] == [("error", "route-type-mismatch"), ("error", "route-before-def"),
                                                      ("warning", "empty-group"), ("info", "no-viewpoint")]
    assert issues[1].line == 42


def test_semantic_headers():
    assert parse_semantic("# Semantic Check: All Clear\n\nNo semantic issues found.") == []
    parse = parse_semantic("# Semantic Check: Parse Error\n\nStart tag expected")
    assert parse[0].layer == "xml" and parse[0].severity == "error"
    assert parse_semantic("# Semantic Check: Input Error\n\nProvide either")[0].layer == "input"
    assert parse_semantic("# Semantic Check: No Scene\n\nNo Scene element")[0].layer == "semantic"


def test_xsd_payload():
    issues = parse_xsd({"valid": False, "errors": ["Line 3: Element 'Box', attribute 'solid': 'True' is not valid",
                                                   "XML parse error: Opening and ending tag mismatch"]})
    assert (issues[0].layer, issues[0].line) == ("xsd", 3) and issues[0].message.startswith("Element 'Box'")
    assert issues[1].layer == "xml"


def test_combine_keeps_one_parse_error_and_ok_rules():
    v = combine({"valid": False, "errors": ["XML parse error: boom"]}, "# Semantic Check: Parse Error\n\nboom")
    assert not v.ok and len(v.errors) == 1 and v.layers() == ["xml"]
    v = combine({"valid": True, "errors": []}, REPORT)
    assert not v.ok and v.xsd_valid and v.layers() == ["semantic"] and len(v.warnings) == 1
    v = combine({"valid": True, "errors": []}, "# Semantic Check Report\n\n## Warnings\n\n- **[w]** just a warning\n")
    assert v.ok, "warnings never fail a scene"
    assert "route-type-mismatch" in combine({"valid": True}, REPORT).report()
