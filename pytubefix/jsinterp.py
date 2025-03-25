import datetime
import email.utils
import calendar
from functools import update_wrapper
from contextlib import suppress as compat_contextlib_suppress


def js_to_json(code, vars={}, *, strict=False):
@@ -357,6 +359,48 @@
            return if_false
    return if_true

_NaN = float('nan')
_Infinity = float('inf')

def _js_typeof(expr):
    with compat_contextlib_suppress(TypeError, KeyError):
        return {
            JS_Undefined: 'undefined',
            _NaN: 'number',
            _Infinity: 'number',
            True: 'boolean',
            False: 'boolean',
            None: 'object',
        }[expr]
    for t, n in (
        (compat_basestring, 'string'),
        (compat_numeric_types, 'number'),
    ):
        if isinstance(expr, t):
            return n
    if callable(expr):
        return 'function'
    # TODO: Symbol, BigInt
    return 'object'

def wraps_op(op):

    def update_and_rename_wrapper(w):
        f = update_wrapper(w, op)
        # fn names are str in both Py 2/3
        f.__name__ = str('JS_') + f.__name__
        return f

    return update_and_rename_wrapper

def _js_unary_op(op):

    @wraps_op(op)
    def wrapped(_, a):
        return op(a)

    return wrapped


# Ref: https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Operators/Operator_Precedence
_OPERATORS = {  # None => Defined in JSInterpreter._operator
@@ -391,8 +435,15 @@
    '**': _js_exp,
}

_UNARY_OPERATORS_X = {
    'void': _js_unary_op(lambda _: JS_Undefined),
    'typeof': _js_unary_op(_js_typeof),
}

_COMP_OPERATORS = {'===', '!==', '==', '!=', '<=', '>=', '<', '>'}

_ALL_OPERATORS = {**_OPERATORS,  **_UNARY_OPERATORS_X}

_NAME_RE = r'[a-zA-Z_$][\w$]*'
_MATCHING_PARENS = dict(zip(*zip('()', '{}', '[]')))
_QUOTES = '\'"/'
@@ -565,6 +616,23 @@
        except TypeError:
            return self._named_object(namespace, obj)

    def handle_operators(self, expr, local_vars, allow_recursion):
        for op in _ALL_OPERATORS:
            separated = list(self._separate(expr, op))
            right_expr = separated.pop()
            while True:
                if op in '?<>*-' and len(separated) > 1 and not separated[-1].strip():
                    separated.pop()
                elif not (separated and op == '?' and right_expr.startswith('.')):
                    break
                right_expr = f'{op}{right_expr}'
                if op != '-':
                    right_expr = f'{separated.pop()}{op}{right_expr}'
            if not separated:
                continue
            left_val = self.interpret_expression(op.join(separated), local_vars, allow_recursion)
            return self._operator(op, left_val, right_expr, expr, local_vars, allow_recursion), True

    # @Debugger.wrap_interpreter
    def interpret_statement(self, stmt, local_vars, allow_recursion=100):
        if allow_recursion < 0:
@@ -619,6 +687,16 @@
            left = self.interpret_expression(expr[5:], local_vars, allow_recursion)
            return None, should_return

        for op in _UNARY_OPERATORS_X:
            if not expr.startswith(op):
                continue
            operand = expr[len(op):]
            if not operand or operand[0] != ' ':
                continue
            op_result = self.handle_operators(expr, local_vars, allow_recursion)
            if op_result:
                return op_result[0], should_return

        if expr.startswith('{'):
            inner, outer = self._separate_at_paren(expr)
            # try for object expression (Map)
@@ -662,8 +740,11 @@
        md = m.groupdict() if m else {}
        if md.get('if'):
            cndn, expr = self._separate_at_paren(expr[m.end() - 1:])
            if expr.startswith('{'):
                if_expr, expr = self._separate_at_paren(expr)
            else:
                # may lose ... else ... because of ll.368-374
                if_expr, expr = self._separate_at_paren(' %s;' % (expr,), delim=';')
            else_expr = None
            m = re.match(r'else\s*{', expr)
            if m:
@@ -838,31 +919,25 @@
            return float('NaN'), should_return

        elif m and m.group('return'):
            try:
                return local_vars[m.group('name')], should_return
            except KeyError as e:
                return self.extract_global_var(e.args[0]), should_return

        with contextlib.suppress(ValueError):
            return json.loads(js_to_json(expr, strict=True)), should_return

        if m and m.group('indexing'):
            try:
                val = local_vars[m.group('in')]
            except KeyError as e:
                val = self.extract_global_obj(e.args[0])
            idx = self.interpret_expression(m.group('idx'), local_vars, allow_recursion)
            return self._index(val, idx), should_return

        op_result = self.handle_operators(expr, local_vars, allow_recursion)
        if op_result:
            return op_result[0], should_return













        if m and m.group('attribute'):
            variable, member, nullish = m.group('var', 'member', 'nullish')
@@ -1031,80 +1106,96 @@
            raise self.Exception('Cannot return from an expression', expr)
        return ret

    def extract_global_obj(self, var):
        global_var = re.search(
            fr'''var\s?{re.escape(var)}=[\"\'](?P<var>.*?)\.split\(\"(?P<split>.*?)\"\)''',
            self.code
        )
        code = global_var.group("var").split(global_var.group("split"))
        return code

    def extract_global_var(self, var):
        global_var = re.search(
            fr'''var\s?{re.escape(var)}=(?P<val>.*?);''',
            self.code
        )
        code = global_var.group('val')
        return code

    def extract_object(self, objname):
        _FUNC_NAME_RE = r'''(?:[a-zA-Z$0-9]+|"[a-zA-Z$0-9]+"|'[a-zA-Z$0-9]+')'''
        obj = {}
        obj_m = re.search(
            r'''(?x)
                (?<!\.)%s\s*=\s*{\s*
                    (?P<fields>(%s\s*:\s*function\s*\(.*?\)\s*{.*?}(?:,\s*)?)*)
                }\s*;
            ''' % (re.escape(objname), _FUNC_NAME_RE),
            self.code)
        if not obj_m:
            raise self.Exception(f'Could not find object {objname}')
        fields = obj_m.group('fields')
        # Currently, it only supports function definitions
        r = r'''(?x)
                (?P<key>%s)\s*:\s*function\s*\((?P<args>(?:%s|,)*)\){(?P<code>[^}]+)}
            ''' % (_FUNC_NAME_RE, _NAME_RE)
        fields_m = re.finditer(r, fields)
        for f in fields_m:
            argnames = f.group('args').split(',')
            name = remove_quotes(f.group('key'))
            obj[name] = function_with_repr(self.build_function(argnames, f.group('code')), f'F<{name}>')

        return obj

    def extract_function_code(self, funcname):
        """ @returns argnames, code """
        func_m = re.search(
            r'''(?xs)
                (?:
                    function\s+%(name)s|
                    [{;,]\s*%(name)s\s*=\s*function|
                    (?:var|const|let)\s+%(name)s\s*=\s*function
                )\s*
                \((?P<args>[^)]*)\)\s*
                (?P<code>{.+})''' % {'name': re.escape(funcname)},
            self.code)
        if func_m is None:
            raise self.Exception(f'Could not find JS function "{funcname}"')
        code, _ = self._separate_at_paren(func_m.group('code'))
        return [x.strip() for x in func_m.group('args').split(',')], code

    def extract_function(self, funcname):
        return function_with_repr(
            self.extract_function_from_code(*_fixup_n_function_code(*self.extract_function_code(funcname))),
            f'F<{funcname}>')

    def extract_function_from_code(self, argnames, code, *global_stack):
        local_vars = {}
        while True:
            mobj = re.search(r'function\((?P<args>[^)]*)\)\s*{', code)
            if mobj is None:
                break
            start, body_start = mobj.span()
            body, remaining = self._separate_at_paren(code[body_start - 1:])
            name = self._named_object(local_vars, self.extract_function_from_code(
                [x.strip() for x in mobj.group('args').split(',')],
                body, local_vars, *global_stack))
            code = code[:start] + name + remaining
        return self.build_function(argnames, code, local_vars, *global_stack)

    def call_function(self, funcname, *args):
        return self.extract_function(funcname)(args)

    def build_function(self, argnames, code, *global_stack):
        global_stack = list(global_stack) or [{}]
        argnames = tuple(argnames)

        def resf(args, kwargs={}, allow_recursion=100):
            global_stack[0].update(itertools.zip_longest(argnames, args, fillvalue=None))
            global_stack[0].update(kwargs)
            var_stack = LocalNameSpace(*global_stack)
            ret, should_abort = self.interpret_statement(code.replace('\n', ' '), var_stack, allow_recursion - 1)
            if should_abort:
                return ret

        return resf
