#!/usr/bin/env python3

import ast
import json
import warnings
import outlines

INDENT = 2

class AST39(ast.NodeTransformer):
    def visit_Num(self, node):
        return ast.Constant(node.n)
    def visit_Str(self, node):
        return ast.Constant(node.s)
    def visit_NameConstant(self, node):
        return ast.Constant(node.value)
    def visit_Ellipsis(self, node):
        return ast.Constant(node)
    def visit_ExtSlice(self, node):
        return ast.Tuple([self.generic_visit(d) for d in node.dims])
    def visit_Index(self, node):
        return node.value

    @classmethod
    def parse(cls, str, name='<unknown>'):
        tree = ast.parse(str, name)
        if hasattr(ast, "NameConstant"):
            return ast.fix_missing_locations(cls().visit(tree))
        else:
            return tree

    @staticmethod
    def unparse(tree, explain=False):
        if hasattr(ast, "unparse"):
            return ast.unparse(tree)
        elif explain:
            return "/* Please convert to Python: " + ast.dump(tree) + " */"
        else:
            return ast.dump(tree)

class UnsupportedConstruct(AssertionError): pass

class MissingHint(Exception):
    def __init__(self, tree, key, hint):
        self.message = f"Could not find {key} key for `{AST39.unparse(tree)}`"
        self.key = key
        self.tree = tree
        self.hint = hint

ISSUES = []

def catch_issues(f):
    def wrapped(tree, *args, **kwargs):
        try:
            return f(tree, *args, **kwargs)
        except MissingHint as e:
            ISSUES.append(e)
            return "/* " + AST39.unparse(tree) + " */"
        except AssertionError as e:
            raise e
            try:
                return find_hint(tree, "js")
            except MissingHint as e2:
                ISSUES.append(e2)
                return "/* " + AST39.unparse(tree) + " */"
    return wrapped

HINTS = []

def read_hints(f):
    global HINTS
    hints = json.load(f)
    for h in hints:
        assert "line" in h
        assert isinstance(h["line"], int)
        assert "code" in h
        s = AST39.parse(h["code"])
        assert isinstance(s, ast.Module)
        assert len(s.body) == 1
        assert isinstance(s.body[0], ast.Expr)
        h["ast"] = s.body[0].value
        h["used"] = False
    HINTS = hints
    
def find_hint(t, key):
    for h in HINTS:
        if h["line"] != t.lineno: continue
        if ast.dump(h["ast"]) != ast.dump(t): continue
        if key not in h: continue
        break
    else:
        hint = {"line": t.lineno, "code": AST39.unparse(t, explain=True), key: "???"}
        raise MissingHint(t, key, hint)
    h["used"] = True
    return h[key]

def check_args(args, ctx):
    assert not args.vararg, ast.dump(args)
    assert not args.kwonlyargs, ast.dump(args)
    assert not args.kw_defaults, ast.dump(args)
    assert not args.kwarg, ast.dump(args)
    out = []
    defaults = ([None] * len(args.args) + args.defaults)[-len(args.args):]
    for i, (arg, default) in enumerate(zip(args.args, defaults)):
        assert not arg.annotation
        if ctx.type == "class" and i == 0:
            assert arg.arg == "self"
        else:
            if default:
                out.append(arg.arg + " = " + compile_expr(default, ctx))
            else:
                out.append(arg.arg)
    return out

RENAME_METHODS = {
    "lower": "toLowerCase",
    "upper": "toUpperCase",
    "strip": "trim",
    "append": "push",
    "pop": "pop",
    "startswith": "startsWith",
    "find": "indexOf",
}

RENAME_FNS = {
    "int": "Math.parseInt",
    "float": "Math.parseFloat",
    "print": "console.log",
}

IMPORTS = []

LIBRARY_METHODS = [
    # socket
    "connect",
    "wrap_socket",
    "send",
    "makefile",
    "readline",
    "read",
    "close",

    # tkinter
    "pack",
    "bind",
    "delete",
    "create_text",
    "create_rectangle",

    # tkinter.font
    "metrics",
    "measure",

    # stuff the compiler needs
    "toString",
    "init",
]

OUR_FNS = []
OUR_CLASSES = []
OUR_CONSTANTS = []
OUR_METHODS = []

def load_outline(ol):
    for item in ol:
        if isinstance(item, outlines.IfMain): continue
        elif isinstance(item, outlines.Const):
            OUR_CONSTANTS.extend(item.names)
        elif isinstance(item, outlines.Function):
            OUR_FNS.append(item.name)
        elif isinstance(item, outlines.Class):
            OUR_CLASSES.append(item.name)
            for subitem in item.fns:
                if isinstance(subitem, outlines.Const): continue
                elif isinstance(subitem, outlines.Function):
                    OUR_METHODS.append(subitem.name)
                else:
                    raise ValueError(subitem)
        else:
            raise ValueError(item)
    THEIR_STUFF = set(LIBRARY_METHODS) | set(RENAME_METHODS) | set(RENAME_FNS)
    OUR_STUFF = set(OUR_FNS) | set(OUR_METHODS) | set(OUR_CLASSES) | set(OUR_CONSTANTS)

    mixed_types = set(OUR_FNS) & set(OUR_CLASSES)
    assert not mixed_types, f"Names defined as both class and function: {mixed_types}"
    our_their = (set(LIBRARY_METHODS) | set(RENAME_METHODS)) & set(OUR_METHODS)
    assert not our_their, f"Methods defined by both our code and libraries: {our_their}"
    our_their = set(RENAME_FNS) & set(OUR_FNS)
    assert not our_their, f"Functions defined by our code shadow builtins: {our_their}"

def compile_method(base, name, args, ctx):
    base_js = compile_expr(base, ctx)
    args_js = [compile_expr(arg, ctx) for arg in args]
    if name in LIBRARY_METHODS:
        return base_js + "." + name + "(" + ", ".join(args_js) + ")"
    elif name in OUR_METHODS:
        return "await " + base_js + "." + name + "(" + ", ".join(args_js) + ")"
    elif name in RENAME_METHODS:
        return base_js + "." + RENAME_METHODS[name] + "(" + ", ".join(args_js) + ")"
    elif isinstance(base, ast.Name) and base.id == "self":
        return base_js + "." + name + "(" + ", ".join(args_js) + ")"
    elif base_js in IMPORTS:
        return base_js + "." + name + "(" + ", ".join(args_js) + ")"
    elif name == "format":
        assert isinstance(base, ast.Constant)
        assert isinstance(base.value, str)
        parts = base.value.split("{}")
        assert len(parts) == len(args) + 1
        out = ""
        for part, arg in zip(parts, [None] + args_js):
            assert "{" not in part
            if arg: out += " + " + arg
            if part: out += " + " + compile_expr(ast.Constant(part), ctx)
        return "(" + out[3:] + ")"
    elif name == "encode":
        assert len(args) == 1
        assert isinstance(args[0], ast.Constant)
        assert args[0].value == "utf8"
        return base_js
    elif name == "join":
        assert len(args) == 1
        return args_js[0] + ".join(" + base_js + ")"
    elif name == "isspace":
        assert len(args) == 0
        return base_js + ".match(/^\s*$/)"
    elif name == "items":
        assert len(args) == 0
        return "Object.entries(" + base_js + ")"
    elif name == "split":
        assert 0 <= len(args) <= 2
        if len(args) == 0:
            return base_js + ".trim().split(/\s+/)"
        elif len(args) == 1:
            return base_js + ".split(" + args_js[0] + ")"
        else:
            return "pysplit(" + base_js + ", " + args_js[0] + ", " + args_js[1] + ")"
    else:
        raise UnsupportedConstruct()

def compile_function(name, args, ctx):
    args_js = [compile_expr(arg, ctx) for arg in args]
    if name in RENAME_FNS:
        return RENAME_FNS[name] + "(" + ", ".join(args_js) + ")"
    elif name in OUR_FNS:
        return "await " + name + "(" + ", ".join(args_js) + ")"
    elif name in OUR_CLASSES:
        return "await (new " + name + "()).init(" + ", ".join(args_js) + ")"
    elif name == "len":
        assert len(args) == 1
        return args_js[0] + ".length"
    elif name == "isinstance":
        assert len(args) == 2
        return args_js[0] + " instanceof " + args_js[1]
    elif name == "sum":
        assert len(args) == 1
        return args_js[0] + ".reduce((a, v) => a + v, 0)"
    elif name == "max":
        assert 1 <= len(args) <= 2
        if len(args) == 1:
            return args_js[0] + ".reduce((a, v) => Math.max(a, v))"
        else:
            return "Math.max(" + args_js[0] + ", " + args_js[1] + ")"
    elif name == "breakpoint":
        assert isinstance(args[0], ast.Constant)
        assert isinstance(args[0].value, str)
        return "await breakpoint.event(" + ", ".join(args_js) + ")"
    elif name == "min":
        assert 1 <= len(args) <= 2
        if len(args) == 1:
            return args_js[0] + ".reduce((a, v) => Math.min(a, v))"
        else:
            return "Math.min(" + args_js[0] + ", " + args_js[1] + ")"
    elif name == "repr":
        assert len(args) == 1
        return args_js[0] + ".toString()"
    else:
        raise UnsupportedConstruct()

def op2str(op):
    if isinstance(op, ast.Add): return "+"
    elif isinstance(op, ast.Sub): return "-"
    elif isinstance(op, ast.USub): return "-"
    elif isinstance(op, ast.Mult): return "*"
    elif isinstance(op, ast.Div): return "/"
    elif isinstance(op, ast.Not): return "!"
    elif isinstance(op, ast.Gt): return ">"
    elif isinstance(op, ast.Lt): return "<"
    elif isinstance(op, ast.GtE): return ">="
    elif isinstance(op, ast.LtE): return "<="
    elif isinstance(op, ast.Eq): return "==="
    elif isinstance(op, ast.NotEq): return "!=="
    elif isinstance(op, ast.And): return " && "
    elif isinstance(op, ast.Or): return " || "
    else:
        raise UnsupportedConstruct()

def lhs_targets(tree):
    if isinstance(tree, ast.Name):
        return set([tree.id])
    elif isinstance(tree, ast.Tuple):
        return set().union(*[lhs_targets(t) for t in tree.elts])
    elif isinstance(tree, ast.Attribute):
        return set()
    elif isinstance(tree, ast.Subscript):
        return set()
    else:
        raise UnsupportedConstruct()
    
def compile_lhs(tree, ctx):
    targets = lhs_targets(tree)
    for target in targets:
        if target not in ctx:
            ctx[target] = True
    return compile_expr(tree, ctx)

class Context(dict):
    def __init__(self, type, parent):
        super().__init__(self)
        self.type = type
        self.parent = parent

    def __contains__(self, i):
        return (super().__contains__(i)) or (i in self.parent)

    def __getitem__(self, i):
        if super().__contains__(self, i):
            return super().__getitem__(i)
        else:
            return self.parent[i]
    
@catch_issues
def compile_expr(tree, ctx):
    if isinstance(tree, ast.Subscript):
        lhs = compile_expr(tree.value, ctx)
        if isinstance(tree.slice, ast.Slice):
            assert not tree.slice.step
            lower = tree.slice.lower and compile_expr(tree.slice.lower, ctx)
            upper = tree.slice.upper and compile_expr(tree.slice.upper, ctx)
            if lower and upper:
                return lhs + ".slice(" + lower + ", " + upper + ")"
            elif upper:
                return lhs + ".slice(0, " + upper + ")"
            elif lower:
                return lhs + ".slice(" + lower + ")"
            else:
                return lhs + ".slice()"
        else:
            rhs = compile_expr(tree.slice, ctx)
            if rhs == "(-1)":
                return lhs + "[" + lhs + ".length - 1]"
            else:
                return lhs + "[" + rhs + "]"
    elif isinstance(tree, ast.Call):
        args = tree.args + [kv.value for kv in tree.keywords]
        if isinstance(tree.func, ast.Attribute):
            return "(" + compile_method(tree.func.value, tree.func.attr, args, ctx) + ")"
        elif isinstance(tree.func, ast.Name):
            return "(" + compile_function(tree.func.id, args, ctx) + ")"
        else:
            raise UnsupportedConstruct()
    elif isinstance(tree, ast.UnaryOp):
        rhs = compile_expr(tree.operand, ctx)
        if isinstance(tree.op, ast.Not): rhs = "truthy(" + rhs + ")"
        return "(" + op2str(tree.op) + rhs + ")"
    elif isinstance(tree, ast.BinOp):
        lhs = compile_expr(tree.left, ctx)
        rhs = compile_expr(tree.right, ctx)
        return "(" + lhs + " " + op2str(tree.op) + " " + rhs + ")"
    elif isinstance(tree, ast.BoolOp):
        parts = ["truthy("+compile_expr(val, ctx)+")" for val in tree.values]
        return "(" + (" " + op2str(tree.op) + " ").join(parts) + ")"
    elif isinstance(tree, ast.Compare):
        assert len(tree.ops) == 1
        assert len(tree.comparators) == 1
        lhs = compile_expr(tree.left, ctx)
        rhs = compile_expr(tree.comparators[0], ctx)
        if (isinstance(tree.ops[0], ast.In) or isinstance(tree.ops[0], ast.NotIn)) and \
           isinstance(tree.comparators[0], ast.List):
            negate = isinstance(tree.ops[0], ast.NotIn)
            # pure expressions
            assert isinstance(tree.left, ast.Name) or \
                (isinstance(tree.left, ast.Subscript) and isinstance(tree.left.value, ast.Name)), \
                ast.dump(tree)
            op = " !== " if negate else " === "
            parts = [lhs + op + compile_expr(v, ctx) for v in tree.comparators[0].elts]
            return "(" + (" && " if negate else " || ").join(parts) + ")"
        elif isinstance(tree.ops[0], ast.In) or isinstance(tree.ops[0], ast.NotIn):
            t = find_hint(tree, "type")
            negate = isinstance(tree.ops[0], ast.NotIn)
            assert t in ["str", "dict", "list"]
            cmp = "===" if negate else "!=="
            if t in ["str", "list"]:
                return "(" + rhs + ".indexOf(" + lhs + ") " + cmp + " -1)"
            elif t == "dict":
                return "(" + rhs + "[" + lhs + "] " + cmp + " \"undefined\")"
        elif isinstance(tree.ops[0], ast.Eq) and \
             (isinstance(tree.comparators[0], ast.List) or isinstance(tree.left, ast.List)):
            return "(JSON.stringify(" + lhs + ") === JSON.stringify(" + rhs + "))"
        else:
            return "(" + lhs + " " + op2str(tree.ops[0]) + " " + rhs + ")"
    elif isinstance(tree, ast.IfExp):
        test = compile_expr(tree.test, ctx)
        ift = compile_expr(tree.body, ctx)
        iff = compile_expr(tree.orelse, ctx)
        return "(" + test + " ? " + ift + " : " + iff + ")"
    elif isinstance(tree, ast.ListComp):
        assert len(tree.generators) == 1
        gen = tree.generators[0]
        iterator = compile_expr(gen.iter, ctx)
        ctx2 = Context("expr", ctx)
        arg = compile_lhs(gen.target, ctx2)
        assert not gen.is_async
        assert not gen.ifs
        e = compile_expr(tree.elt, ctx2)
        return iterator + ".map((" + arg + ") => " + e + ")"
    elif isinstance(tree, ast.Attribute):
        base = compile_expr(tree.value, ctx)
        return base + "." + tree.attr
    elif isinstance(tree, ast.Dict):
        pairs = [compile_expr(k, ctx) + ": " + compile_expr(v, ctx) for k, v in zip(tree.keys, tree.values)]
        return "{" + ", ".join(pairs) + "}"
    elif isinstance(tree, ast.Tuple) or isinstance(tree, ast.List):
        return "[" + ", ".join([compile_expr(a, ctx) for a in tree.elts]) + "]"
    elif isinstance(tree, ast.Name):
        assert tree.id in ctx, f"Could not find variable {tree.id}"
        return "this" if tree.id == "self" else tree.id
    elif isinstance(tree, ast.Constant):
        if isinstance(tree.value, str):
            return compile_str(tree.value)
        elif isinstance(tree.value, bool):
            return "true" if tree.value else "false"
        elif isinstance(tree.value, int):
            return repr(tree.value)
        elif isinstance(tree.value, float):
            return repr(tree.value)
        elif tree.value is None:
            return "null"
        else:
            raise UnsupportedConstruct()
    else:
        raise UnsupportedConstruct()

def compile_str(s):
    out = repr(s)
    if out[0] == out[-1] == "'" and '"' not in out:
        out = '"' + out[1:-1] + '"'
    return out

def flatten_ifs(tree):
    parts = [(tree.test, tree.body)]
    while len(tree.orelse) == 1 and isinstance(tree.orelse[0], ast.If):
        tree = tree.orelse[0]
        parts.append((tree.test, tree.body))
    if tree.orelse:
        parts.append((None, tree.orelse))
    return parts

@catch_issues
def compile(tree, ctx, indent=0):
    if isinstance(tree, ast.Import):
        assert len(tree.names) == 1
        assert not tree.names[0].asname
        name = tree.names[0].name
        ctx[name] = True
        IMPORTS.append(name)

        return " " * indent + "// Please configure the '" + name + "' module"
    elif isinstance(tree, ast.ClassDef):
        assert not tree.bases
        assert not tree.keywords
        assert not tree.decorator_list
        ctx[tree.name] = True
        ctx2 = Context("class", ctx)
        parts = [compile(part, indent=indent + INDENT, ctx=ctx2) for part in tree.body]
        return " " * indent + "class " + tree.name + " {\n" + "\n\n".join(parts) + "\n}"
    elif isinstance(tree, ast.FunctionDef):
        assert not tree.decorator_list
        assert not tree.returns
        args = check_args(tree.args, ctx)

        ctx2 = Context("function", ctx)
        for arg in tree.args.args:
            ctx2[arg.arg] = True
        body = "\n".join([compile(line, indent=indent + INDENT, ctx=ctx2) for line in tree.body])

        if tree.name == "__init__":
            # JS constructors cannot be async, so we move that to a builder method
            assert ctx.type == "class"
            def_line = " " * indent + "async init(" + ", ".join(args) + ") {\n"
            ret_line = "\n" + " " * (indent + INDENT) + "return this;"
            last_line = "\n" + " " * indent + "}"
            return def_line + body + ret_line + last_line
        elif tree.name == "__repr__":
            # This actually defines a 'toString' operator
            assert ctx.type == "class"
            def_line = " " * indent + "toString(" + ", ".join(args) + ") {\n"
            last_line = "\n" + " " * indent + "}"
            return def_line + body + last_line
        else:
            kw = "" if ctx.type == "class" else "function "
            def_line = kw + tree.name + "(" + ", ".join(args) + ") {\n"
            if not tree.name.startswith("__"):
                def_line = "async " + def_line
            last_line = "\n" + " " * indent + "}"
            return " " * indent + def_line + body + last_line
    elif isinstance(tree, ast.Expr) and ctx.type == "module" and \
         isinstance(tree.value, ast.Constant) and isinstance(tree.value.value, str):
        cmt = " " * indent + "// "
        return cmt + tree.value.value.strip("\n").replace("\n", "\n" + cmt)
    elif isinstance(tree, ast.Expr):
        return " " * indent + compile_expr(tree.value, ctx) + ";"
    elif isinstance(tree, ast.Assign):
        assert len(tree.targets) == 1

        targets = lhs_targets(tree.targets[0])
        ins = set([target in ctx for target in targets])
        if True in ins and False in ins:
            kw = "let " + ", ".join([target for target in targets if target not in ctx]) + "; "
        elif ctx.type in ["class"]: kw = ""
        elif False in ins: kw = "let "
        else: kw = ""

        lhs = compile_lhs(tree.targets[0], ctx)
        rhs = compile_expr(tree.value, ctx)
        return " " * indent + kw + lhs + " = " + rhs + ";"
    elif isinstance(tree, ast.AugAssign):
        targets = lhs_targets(tree.target)
        for target in targets:
            assert target in ctx
        lhs = compile_lhs(tree.target, ctx)
        rhs = compile_expr(tree.value, ctx)
        return " " * indent + lhs + " " + op2str(tree.op) + "= " + rhs + ";"
    elif isinstance(tree, ast.Assert):
        test = compile_expr(tree.test, ctx)
        msg = compile_expr(tree.msg, ctx) if tree.msg else None
        return " " * indent + "console.assert(" + test + (", " + msg if msg else "") + ");"
    elif isinstance(tree, ast.Return):
        ret = compile_expr(tree.value, ctx) if tree.value else None
        return " " * indent + "return" + (" " + ret if ret else "") + ";"
    elif isinstance(tree, ast.While):
        assert not tree.orelse
        test = compile_expr(tree.test, ctx)
        out = " " * indent + "while (" + test + ") {\n"
        out += "\n".join([compile(line, indent=indent + INDENT, ctx=ctx) for line in tree.body])
        out += "\n" + " " * indent + "}"
        return out
    elif isinstance(tree, ast.For):
        assert not tree.orelse
        ctx2 = Context(ctx.type, ctx)
        lhs = compile_lhs(tree.target, ctx2)
        rhs = compile_expr(tree.iter, ctx)
        body = "\n".join([compile(line, indent=indent + INDENT, ctx=ctx2) for line in tree.body])
        fstline = " " * indent + "for (let " + lhs + " of " + rhs + ") {\n"
        return fstline + body + "\n" + " " * indent + "}"
    elif isinstance(tree, ast.If) and ctx.type == "module":
        test = tree.test
        assert isinstance(test, ast.Compare)
        assert isinstance(test.left, ast.Name)
        assert test.left.id == "__name__"
        assert len(test.comparators) == 1
        if isinstance(test.comparators[0], ast.Str):
            s = test.comparators[0].s
        else:
            assert isinstance(test.comparators[0], ast.Constant)
            assert isinstance(test.comparators[0].value, str)
            s = test.comparators[0].value
        assert s == "__main__"
        assert len(test.ops) == 1
        assert isinstance(test.ops[0], ast.Eq)
        return " " * indent + "// Requires a test harness\n"
    elif isinstance(tree, ast.If):
        if not tree.orelse and tree.test.lineno == tree.body[0].lineno:
            assert len(tree.body) == 1
            ctx2 = Context(ctx.type, ctx)
            test = compile_expr(tree.test, ctx)
            body = compile(tree.body[0], indent=indent, ctx=ctx2)
            return " " * indent + "if (truthy(" + test + ")) " + body.strip()
        else:
            parts = flatten_ifs(tree)
            out = " " * indent

            # This block handles variables defined in all branches of an if statement
            ctxs = []
            for test, body in parts:
                ctx2 = Context(ctx.type, ctx)
                ctxs.append(ctx2)
                for line in body: compile(line, ctx=ctx2)

            intros = set.intersection(*[set(ctx2) for ctx2 in ctxs]) - set(ctx)
            if intros:
                for name in intros: ctx[name] = True
                out += "let " + ",".join(intros) + ";\n" + " " * indent

            for i, (test, body) in enumerate(parts):
                ctx2 = Context(ctx.type, ctx)
                body_js = "\n".join([compile(line, indent=indent + INDENT, ctx=ctx2) for line in body])
                if not i and test:
                    test_js = compile_expr(test, ctx)
                    out += "if (truthy(" + test_js + ")) {\n"
                elif i and test:
                    test_js = compile_expr(test, ctx)
                    out += " else if (truthy(" + test_js + ")) {\n"
                elif not test:
                    out += " else {\n"
                out += body_js + "\n"
                out += " " * indent + "}"

            return out
    elif isinstance(tree, ast.Continue):
        return " " * indent + "continue;"
    elif isinstance(tree, ast.Break):
        return " " * indent + "break;"
    else:
        raise UnsupportedConstruct()
    
def compile_module(tree, name):
    assert isinstance(tree, ast.Module)
    ctx = Context("module", {})

    items = [compile(item, indent=0, ctx=ctx) for item in tree.body]
    return "\n\n".join(items)

if __name__ == "__main__":
    import sys, os
    import argparse

    MIN_PYTHON = (3, 7)
    if sys.version_info < MIN_PYTHON:
        sys.exit("Python %s.%s or later is required.\n" % MIN_PYTHON)

    parser = argparse.ArgumentParser(description="Compiles each chapter's Python code to JavaScript")
    parser.add_argument("--hints", default=None, type=argparse.FileType())
    parser.add_argument("--indent", default=2, type=int)
    parser.add_argument("python", type=argparse.FileType())
    parser.add_argument("javascript", type=argparse.FileType("w"))
    args = parser.parse_args()

    name = os.path.basename(args.python.name)
    assert name.endswith(".py")
    if args.hints: read_hints(args.hints)
    INDENT = args.indent
    tree = AST39.parse(args.python.read(), args.python.name)
    load_outline(outlines.outline(tree))
    js = compile_module(tree, name[:-len(".py")])
    args.javascript.write(js)

    issues = 0
    for i in ISSUES:
        print(i.message)
        if i.hint:
            print("  Hint:", json.dumps(i.hint), file=sys.stderr)
        issues += 1

    for h in HINTS:
        if h["used"]: continue
        h2 = h.copy()
        del h2["used"]
        del h2["ast"]
        print(f"Unused hint: {json.dumps(h2)}", file=sys.stderr)
        issues += 1

    sys.exit(issues)

