"""Small, valid X3D scenes used as starting points, fallbacks (no API key) and Break-it bases."""

LANTERN = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Immersive" version="4.0">
  <head>
    <meta name="title" content="Spinning lantern"/>
    <meta name="creator" content="x3d.lab sample"/>
  </head>
  <Scene>
    <NavigationInfo type='"EXAMINE" "ANY"'/>
    <Background skyColor="0.05 0.07 0.15"/>
    <Viewpoint description="front" position="0 1.6 8" orientation="1 0 0 -0.12"/>
    <DirectionalLight direction="-0.3 -1 -0.4" intensity="0.8"/>
    <PointLight location="0 3 3" intensity="0.6"/>
    <Transform DEF="Spinner" translation="0 1 0">
      <Shape DEF="Body">
        <Appearance>
          <Material DEF="Glow" diffuseColor="0.95 0.55 0.1" emissiveColor="0.3 0.12 0"/>
        </Appearance>
        <Box size="1 1.4 1"/>
      </Shape>
      <Transform translation="0 1 0">
        <Shape>
          <Appearance>
            <Material diffuseColor="0.6 0.1 0.1"/>
          </Appearance>
          <Cone bottomRadius="0.9" height="0.6"/>
        </Shape>
      </Transform>
    </Transform>
    <Transform translation="2.5 0.7 0" scale="0.6 0.6 0.6">
      <Shape USE="Body"/>
    </Transform>
    <Transform translation="0 -0.1 0">
      <Shape>
        <Appearance>
          <Material diffuseColor="0.2 0.5 0.25"/>
        </Appearance>
        <Cylinder radius="4" height="0.2"/>
      </Shape>
    </Transform>
    <TimeSensor DEF="Clock" cycleInterval="6" loop="true"/>
    <OrientationInterpolator DEF="Turn" key="0 0.5 1" keyValue="0 1 0 0 0 1 0 3.14159 0 1 0 6.28318"/>
    <ROUTE fromNode="Clock" fromField="fraction_changed" toNode="Turn" toField="set_fraction"/>
    <ROUTE fromNode="Turn" fromField="value_changed" toNode="Spinner" toField="set_rotation"/>
  </Scene>
</X3D>
"""

PAGODA = """<?xml version="1.0" encoding="UTF-8"?>
<X3D profile="Immersive" version="4.0">
  <head><meta name="title" content="Pagoda"/></head>
  <Scene>
    <NavigationInfo type='"EXAMINE" "ANY"'/>
    <Background skyColor="0.4 0.6 0.95"/>
    <Viewpoint description="front" position="0 3 14" orientation="1 0 0 -0.15"/>
    <DirectionalLight direction="-0.4 -1 -0.5" intensity="0.9"/>
    <Shape><Appearance><Material diffuseColor="0.2 0.6 0.25"/></Appearance><Cone bottomRadius="8" height="3"/></Shape>
    <Transform translation="0 2.4 0"><Shape><Appearance><Material diffuseColor="0.8 0.1 0.1"/></Appearance><Box size="3 1.2 3"/></Shape></Transform>
    <Transform translation="0 5.2 0"><Shape><Appearance><Material diffuseColor="0.9 0.2 0.15"/></Appearance><Cone bottomRadius="1.8" height="1.4"/></Shape></Transform>
  </Scene>
</X3D>
"""

SAMPLES = {"lantern": LANTERN, "pagoda": PAGODA}
