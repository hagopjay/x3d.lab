"""A stand-in `unreal` module, enough to run the lab's Unreal-side scripts' control flow outside Unreal.

It is NOT Unreal: it accepts any attribute and call (returning more stand-ins), records what the scripts did, and
simulates the few behaviours the scripts depend on: the command line, a level's actors, an Interchange scene
import that spawns mesh actors (placed with a chosen axis mapping), the asset registry, Movie Render Queue jobs.
Passing here proves the scripts compile and follow their flow; it proves nothing about Unreal's real API names.
"""
import sys
import types

LOG = []
STATE = {"actors": [], "assets": {}, "cmdline": "", "import_axes": "x,z,y", "import_points": [], "pipelines": [], "opened": []}


class Vec:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x, self.y, self.z = x, y, z


class Obj:
    """Anything: attribute reads give callables / sub-objects; editor properties are stored."""

    def __init__(self, _name="obj", *a, **kw):
        self.__dict__["_name"] = _name
        self.__dict__["_props"] = dict(kw)

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        if k in self._props:
            return self._props[k]
        return Call("%s.%s" % (self._name, k))

    def __setattr__(self, k, v):
        self._props[k] = v

    def set_editor_property(self, k, v):
        self._props[k] = v

    def get_editor_property(self, k):
        if k not in self._props:
            self._props[k] = Obj(k)
        return self._props[k]

    def get_path_name(self):
        return self._props.get("path", "/Game/stub/%s.%s" % (self._name, self._name))

    def __iter__(self):
        return iter([])

    def __bool__(self):
        return True


class Call:
    def __init__(self, name):
        self.name = name

    def __call__(self, *a, **kw):
        LOG.append(self.name)
        return Obj(self.name)


class Actor(Obj):
    def __init__(self, cls, loc=None, rot=None, mesh=None):
        super().__init__(cls)
        self._props.update(cls=cls, loc=loc or Vec(), tags=[], mesh=mesh, hidden=False, label=cls)

    def get_class(self):
        return Obj("class", get_name=lambda: self._props["cls"])

    def get_actor_location(self):
        return self._props["loc"]

    def get_actor_bounds(self, only_colliding):
        return self._props["loc"], Vec(10, 10, 10)

    def get_actor_label(self):
        return self._props["label"]

    def set_actor_label(self, t):
        self._props["label"] = t

    def set_actor_hidden_in_game(self, h):
        self._props["hidden"] = h

    def get_component_by_class(self, c):
        return self.get_editor_property("light_component")

    @property
    def tags(self):
        return self._props["tags"]

    def get_editor_property(self, k):
        if k == "static_mesh_component":
            mesh = self._props["mesh"]
            return Obj("smc", static_mesh=mesh)
        if k == "tags":
            return list(self._props["tags"])
        return super().get_editor_property(k)

    def set_editor_property(self, k, v):
        if k == "tags":
            self._props["tags"] = [str(t) for t in v]
        else:
            super().set_editor_property(k, v)


class Meta(type):
    def __getattr__(cls, k):
        if k.startswith("__"):
            raise AttributeError(k)
        fn = OVERRIDES.get((cls.__name__, k))
        if fn is not None:
            return fn
        return Call("%s.%s" % (cls.__name__, k))


class Stub(Obj, metaclass=Meta):
    def __init__(self, *a, **kw):
        super().__init__(type(self).__name__, **kw)


def parse_command_line(cmd):
    params = {}
    for tok in cmd.split():
        if tok.startswith("-") and "=" in tok:
            k, v = tok[1:].split("=", 1)
            params[k] = v
    return [], [], params


def import_scene(content_path, sd, params):
    from x3due import convert
    for i, p in enumerate(STATE["import_points"]):
        m = Obj("StaticMesh_%d" % (i % 2), path="/Game/X/SM_%d.SM_%d" % (i % 2, i % 2), get_num_triangles=lambda lod: 12)
        STATE["actors"].append(Actor("StaticMeshActor", Vec(*convert.to_ue_point(p, STATE["import_axes"])), mesh=m))
    STATE["actors"].append(Actor("DirectionalLight"))
    STATE["actors"].append(Actor("CineCameraActor", Vec(1, 2, 3)))
    for k in ("SM_0", "SM_1"):
        STATE["assets"]["/Game/X/%s" % k] = "StaticMesh"
    STATE["assets"]["/Game/X/MI_0"] = "MaterialInstanceConstant"
    return True


class ActorSubsystem:
    def get_all_level_actors(self):
        return list(STATE["actors"])

    def spawn_actor_from_class(self, cls, loc, rot):
        a = Actor(cls.__name__, loc)
        STATE["actors"].append(a)
        return a

    def destroy_actor(self, a):
        STATE["actors"].remove(a)
        return True


def get_editor_subsystem(cls):
    if cls.__name__ == "EditorActorSubsystem":
        return ActorSubsystem()
    return Obj(cls.__name__)


def create_asset(name, path, cls, factory):
    p = "%s/%s" % (path, name)
    STATE["assets"][p] = cls.__name__
    o = Obj(name, path=p + "." + name)
    return o


def find_asset_data(p):
    return Obj("ad", asset_class_path=Obj("cp", asset_name=STATE["assets"].get(p, "?")))


def get_all_actors_with_tag(world, tag):
    return [a for a in STATE["actors"] if str(tag) in a.tags]


def open_level(world, path, absolute, options):
    STATE["opened"].append(path)


OVERRIDES = {
    ("SystemLibrary", "parse_command_line"): parse_command_line,
    ("SystemLibrary", "get_command_line"): lambda: STATE["cmdline"],
    ("SystemLibrary", "get_engine_version"): lambda: "5.8.4-stub",
    ("InterchangeManager", "get_interchange_manager_scripted"): lambda: Obj("mgr", import_scene=import_scene,
                                                                             can_translate_source_data=lambda sd, s=False: True),
    ("InterchangeManager", "create_source_data"): lambda f: Obj("sd", file=f),
    ("AssetToolsHelpers", "get_asset_tools"): lambda: Obj("tools", create_asset=create_asset),
    ("EditorAssetLibrary", "list_assets"): lambda path, rec=True, folders=False: [p for p in STATE["assets"] if p.startswith(path)],
    ("EditorAssetLibrary", "find_asset_data"): find_asset_data,
    ("EditorAssetLibrary", "does_asset_exist"): lambda p: p in STATE["assets"],
    ("EditorAssetLibrary", "load_asset"): lambda p: Obj("asset", path=p),
    ("GameplayStatics", "get_all_actors_with_tag"): get_all_actors_with_tag,
    ("GameplayStatics", "open_level"): open_level,
}


class MoviePipelinePythonHostExecutor:
    target_pipeline_class = None

    def __init__(self):
        self._post_init()

    def _post_init(self):
        pass

    def get_last_loaded_world(self):
        return Obj("world")

    def on_executor_finished_impl(self):
        LOG.append("executor finished")

    def on_begin_frame(self):
        pass


class FakePipeline:
    def __init__(self):
        self.on_movie_pipeline_work_finished_delegate = Obj("delegate", add_function_unique=lambda o, n: None)
        STATE["pipelines"].append(self)

    def initialize(self, job):
        self.job = job


class Queue(Obj):
    def __init__(self):
        super().__init__("queue")
        self._props["jobs"] = []

    def allocate_new_job(self, cls):
        j = Obj("job", get_configuration=lambda: Obj("cfg", find_or_add_setting_by_class=lambda c: Obj(c.__name__)))
        self._props["jobs"].append(j)
        return j

    def delete_all_jobs(self):
        self._props["jobs"] = []


def new_object(cls, outer=None, base_type=None):
    if cls is None or getattr(cls, "__name__", "") in ("MoviePipeline",):
        return FakePipeline()
    if getattr(cls, "__name__", "") == "MoviePipelineQueue":
        return Queue()
    return Obj("new")


def make():
    m = types.ModuleType("unreal")
    known = {"Vector": lambda *a: Vec(*a), "Rotator": lambda **kw: Obj("rot", **kw), "LinearColor": lambda *a: Obj("col"),
             "Name": str, "log": lambda s: LOG.append(s), "log_error": lambda s: LOG.append("ERROR " + s),
             "log_warning": lambda s: LOG.append("WARN " + s), "get_editor_subsystem": get_editor_subsystem,
             "uclass": lambda *a, **k: (lambda c: c), "ufunction": lambda *a, **k: (lambda f: f),
             "uproperty": lambda *a, **k: None, "new_object": new_object,
             "MoviePipelinePythonHostExecutor": MoviePipelinePythonHostExecutor,
             "SoftObjectPath": lambda p: p, "IntPoint": lambda w, h: (w, h), "DirectoryPath": lambda p: p,
             "FrameRate": lambda a, b: (a, b), "Paths": Obj("Paths", project_saved_dir=lambda: "/tmp")}
    classes = {}

    def getattr_(name):
        if name in known:
            return known[name]
        if name not in classes:
            classes[name] = Meta(name, (Stub,), {})
        return classes[name]

    m.__getattr__ = getattr_
    return m


def install():
    LOG.clear()
    STATE.update(actors=[], assets={}, cmdline="", import_axes="x,z,y", import_points=[], pipelines=[], opened=[])
    sys.modules["unreal"] = make()
    return sys.modules["unreal"]
