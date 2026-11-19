"""A minimal fake `streamlit` used to execute the real page code without a browser.

Widgets return their default values; labels listed in `pressed` behave as clicked; `values` overrides inputs by label.
st.rerun() raises StopApp (like Streamlit, it halts the script).
"""
import types


class StopApp(Exception):
    pass


class Stub:
    def __init__(self):
        self.reset()

    def reset(self):
        self.seen, self.duplicates, self._form = set(), [], None
        self.session_state = {}
        self.pressed, self.values, self.checked = set(), {}, False
        self.nav_choice = None
        self.errors, self.successes, self.warnings = [], [], []

    # context-manager behaviour (columns, tabs, forms, expanders, sidebar, spinner ...)
    def __enter__(self): return self
    def __exit__(self, *a):
        self._form = None
        return False

    def _register(self, kind, label, key):
        """Streamlit raises DuplicateWidgetID for repeated explicit keys, or identical unkeyed widgets in one scope."""
        ident = ("key", key) if key else (kind, label, self._form)
        if ident in self.seen:
            self.duplicates.append(ident)
        self.seen.add(ident)
    @property
    def sidebar(self): return self

    # ---- layout
    def columns(self, spec, **kw): return [self] * (spec if isinstance(spec, int) else len(spec))
    def tabs(self, labels): return [self] * len(labels)
    def expander(self, *a, **k): return self
    def form(self, key=None, *a, **k):
        self._form = key
        return self
    def container(self, *a, **k): return self
    def spinner(self, *a, **k): return self

    # ---- inputs
    def _val(self, label, default): return self.values.get(label, default)
    def text_input(self, label, value="", **k): return self._val(label, value)
    def text_area(self, label, value="", **k): return self._val(label, value)
    def number_input(self, label, min_value=None, max_value=None, value=None, step=None, **k):
        return self._val(label, value if value is not None else min_value)
    def slider(self, label, min_value=None, max_value=None, value=None, step=None, **k): return self._val(label, value)
    def checkbox(self, label, value=False, **k): return self._val(label, value or self.checked)
    def date_input(self, label, value=None, **k): return self._val(label, value)
    def time_input(self, label, value=None, **k): return self._val(label, value)
    def selectbox(self, label, options=(), index=0, format_func=str, **k):
        options = list(options)
        if label in self.values:
            return self.values[label]
        return options[index] if options else None
    def multiselect(self, label, options=(), default=None, **k): return self._val(label, list(default or []))
    def radio(self, label, options=(), **k):
        options = list(options)
        if label == "Navigation" and self.nav_choice in options:
            return self.nav_choice
        return options[0] if options else None
    def _clicked(self, label): return any(p in label for p in self.pressed)
    def button(self, label, *a, **k): return self._clicked(label)
    def form_submit_button(self, label="Submit", *a, **k): return self._clicked(label)
    def download_button(self, *a, **k): return False

    # ---- messages
    def error(self, msg, *a, **k): self.errors.append(str(msg))
    def success(self, msg, *a, **k): self.successes.append(str(msg))
    def warning(self, msg, *a, **k): self.warnings.append(str(msg))
    def rerun(self): raise StopApp()

    # ---- decorators / misc
    def cache_resource(self, *a, **k):
        if len(a) == 1 and callable(a[0]):
            return a[0]
        return lambda fn: fn

    def __getattr__(self, name):          # markdown, write, dataframe, charts, metric, caption, info, ...
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


def _track(name):
    original = getattr(Stub, name)

    def wrapper(self, label="", *a, **k):
        self._register(name, label if isinstance(label, str) else str(label), k.get("key"))
        return original(self, label, *a, **k)
    wrapper.__name__ = name
    return wrapper


for _name in ("text_input", "text_area", "number_input", "slider", "checkbox", "date_input", "time_input",
              "selectbox", "multiselect", "radio", "button", "form_submit_button", "download_button"):
    setattr(Stub, _name, _track(_name))


def install():
    import sys
    stub = Stub()
    mod = types.ModuleType("streamlit")
    mod.__dict__.update({n: getattr(stub, n) for n in dir(stub) if not n.startswith("__")})
    mod.__getattr__ = lambda name: getattr(stub, name)
    mod.session_state = stub.session_state
    sys.modules["streamlit"] = stub  # module-like: attribute access is all the app uses
    return stub
