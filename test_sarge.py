# -*- coding: utf-8 -*-
#
# Copyright (C) 2012-2013 Vinay M. Sajip. See LICENSE for licensing information.
#
# Test harness for sarge: Subprocess Allegedly Rewards Good Encapsulation :-)
#

from __future__ import unicode_literals

from io import TextIOWrapper
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

from sarge import (shell_quote, Capture, Command, CommandLineParser, Pipeline,
                   shell_format, run, parse_command_line, capture_stdout,
                   get_stdout, capture_stderr, get_stderr, capture_both,
                   get_both, Popen, Feeder)
from sarge.shlext import shell_shlex
from stack_tracer import start_trace, stop_trace

if sys.platform == 'win32':  #pragma: no cover
    from sarge.utils import which, find_command, winreg

    HKCR = winreg.HKEY_CLASSES_ROOT

TRACE_THREADS = sys.platform not in ('cli',)    # debugging only

PY3 = sys.version_info[0] >= 3

pyrunner_re = re.compile(r'.*py.*\.exe', re.I)
pywrunner_re = re.compile(r'.*py.*w\.exe', re.I)

def found_file(fn, locations):
    if os.path.exists(fn):
        return '.'
    for d in locations:
        p = os.path.join(d, fn)
        if os.path.exists(p):
            return d
    return None

paths = ['.', ] + os.environ['PATH'].split(os.pathsep)

BINARIES = (
    'cat', 'echo', 'false', 'sleep', 'tee', 'touch', 'true',
)
if os.name == 'nt':  #pragma: no cover
    BINARIES = [exe + '.exe' for exe in BINARIES]

locations = dict([(exe, found_file(exe, paths))
                  for exe in BINARIES])
missing = [exe for exe, path in locations.items() if not path]
if missing:
    exe_list = '%s and %s' % (', '.join(BINARIES[:-1]), BINARIES[-1])
    missing_list = '%s and %s' % (', '.join(missing[:-1]), missing[-1])
    if os.name == 'nt':  #pragma: no cover
        import textwrap
        raise ImportError(textwrap.dedent("""\
            To run these tests on Windows, you need the GnuWin32 coreutils package.
            This appears not to be installed correctly, as the files %s do not appear to be in the current directory.
            See http://gnuwin32.sourceforge.net/packages/coreutils.htm for download details.
            Once downloaded and installed, you need to copy %s to the test directory or have the directory they were installed to on the PATH.""" % (missing_list, exe_list)))
    else:
        raise ImportError(
           'coreutils binaries %s not found' % missing_list)

locations = set(locations.values())
if len(locations) > 1:
    raise ImportError(
        'coreutils binaries %r found spread across multiple path locations %r' %
        (BINARIES, list(locations)))

location = locations.pop()
if os.name != 'nt':
    location = location.replace('bin', 'lib')
import glob
for lib in ('iconv', 'intl'):
    if os.name == 'nt':
        # msys dlls are prefixed with `msys-` instead of `lib`,
        # and have different so versions than GnuWin32
        pattern = os.path.join(location, '*' + lib + '*.dll')
    else:
        pattern = os.path.join(location, 'lib' + lib + '*.so')
    matches = glob.glob(pattern)
    if not matches:
        import warnings
        warnings.warn(
           'coreutils dependency %s not found in %s' % (pattern, location))

logger = logging.getLogger(__name__)

EMITTER = '''#!/usr/bin/env python
import sys

sys.stdout.write('foo\\n')
sys.stderr.write('bar\\n')
'''

SEP = '=' * 60

class SargeTest(unittest.TestCase):
    def setUp(self):
        logger.debug(SEP)
        logger.debug(self.id().rsplit('.', 1)[-1])
        logger.debug(SEP)

    def test_quote(self):
        self.assertEqual(shell_quote(''), "''")
        self.assertEqual(shell_quote('a'), 'a')
        self.assertEqual(shell_quote('*'), "'*'")
        self.assertEqual(shell_quote('foo'), 'foo')
        self.assertEqual(shell_quote("'*.py'"), "''\\''*.py'\\'''")
        self.assertEqual(shell_quote("'a'; rm -f b; true 'c'"),
                                     "''\\''a'\\''; rm -f b; true '\\''c'\\'''")
        self.assertEqual(shell_quote("*.py"), "'*.py'")
        self.assertEqual(shell_quote("'*.py"), "''\\''*.py'")

    def test_quote_with_shell(self):
        from subprocess import PIPE, Popen

        if os.name != 'posix':  #pragma: no cover
            raise unittest.SkipTest('This test works only on POSIX')

        workdir = tempfile.mkdtemp()
        try:
            s = "'\\\"; touch %s/foo #'" % workdir
            cmd = 'echo %s' % shell_quote(s)
            p = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
            p.communicate()
            self.assertEqual(p.returncode, 0)
            files = os.listdir(workdir)
            self.assertEqual(files, [])
            fn = "'ab?'"
            cmd = 'touch %s/%s' % (workdir, shell_quote(fn))
            p = Popen(cmd, shell=True, stdout=PIPE, stderr=PIPE)
            p.communicate()
            self.assertEqual(p.returncode, 0)
            files = os.listdir(workdir)
            self.assertEqual(files, ["'ab?'"])
        finally:
            shutil.rmtree(workdir)

    def test_formatter(self):
        self.assertEqual(shell_format('ls {0}', '*.py'), "ls '*.py'")
        self.assertEqual(shell_format('ls {0!s}', '*.py'), "ls *.py")

    def send_to_capture(self, c, s):
        rd, wr = os.pipe()
        c.add_stream(os.fdopen(rd, 'rb'))
        os.write(wr, s)
        os.close(wr)

    def test_capture(self):
        logger.debug('test_capture started')
        with Capture() as c:
            self.send_to_capture(c, b'foofoo')
            self.assertEqual(c.read(3), b'foo')
            self.assertEqual(c.read(3), b'foo')
            self.assertEqual(c.read(), b'')
        logger.debug('test_capture finished')

    def test_command_splitting(self):
        logger.debug('test_command started')
        cmd = 'echo foo'
        c = Command(cmd)
        self.assertEqual(c.args, cmd.split())
        c = Command(cmd, shell=True)
        self.assertEqual(c.args, cmd)

    def test_command_no_stdin(self):
        self.assertRaises(ValueError, Command, 'cat', stdin='xyz')

    def test_literal_input(self):
        with Capture() as out:
            self.assertEqual(run('cat', stdout=out, input='foo').returncode, 0)
            self.assertEqual(out.read(), b'foo')

    def test_read_extra(self):
        with Capture() as out:
            self.assertEqual(run('cat', stdout=out, input='bar').returncode, 0)
            self.assertEqual(out.read(5), b'bar')

    def test_shell_redirection(self):
        with Capture() as err:
            self.assertEqual(run('cat >&2', stderr=err, shell=True,
                                 input='bar').returncode, 0)
            self.assertEqual(err.read(), b'bar')

    def test_capture_bytes(self):
        with Capture() as err:
            self.assertEqual(run('cat >&2', stderr=err, shell=True,
                                 input='bar').returncode, 0)
        self.assertEqual(err.bytes, b'bar')
        with Capture() as err:
            self.assertEqual(run('cat >&2', stderr=err, shell=True,
                                 input='bar').returncode, 0)
        self.assertEqual(err.text, 'bar')

    def ensure_testfile(self):
        if not os.path.exists('testfile.txt'):  #pragma: no cover
            with open('testfile.txt', 'w') as f:
                for i in range(10000):
                    f.write('Line %d\n' % (i + 1))

    def test_run_sync(self):
        self.ensure_testfile()
        with open('testfile.txt') as f:
            content = f.readlines()
        with Capture() as out:
            self.assertEqual(
                run('cat testfile.txt testfile.txt', stdout=out).returncode, 0)
            lines = out.readlines()
            self.assertEqual(len(lines), len(content) * 2)
        # run with a list (see Issue #3)
        with Capture() as out:
            self.assertEqual(
                run(['cat', 'testfile.txt', 'testfile.txt'],
                    stdout=out).returncode, 0)
            lines = out.readlines()
            self.assertEqual(len(lines), len(content) * 2)

    def test_run_async(self):
        self.ensure_testfile()
        with open('testfile.txt', 'rb') as f:
            content = f.read().splitlines(True)
        with Capture(timeout=1) as out:
            p = run('cat testfile.txt testfile.txt', stdout=out,
                    async_=True)
            # Do some other work in parallel, including reading from the
            # concurrently running child process
            read_count = 0
            if out.readline():
                read_count += 1
            if out.readline():
                read_count += 1
            # kill some time ...
            for i in range(10):
                with open('testfile.txt') as f:
                    f.read()
            p.wait()
            self.assertEqual(p.returncode, 0)
            lines = out.readlines()
            self.assertEqual(len(lines), len(content) * 2 - read_count)

    def test_env(self):
        e = os.environ
        if PY3:
            env = {'FOO': 'BAR'}
        else:
            # Python 2.x wants native strings, at least on Windows
            # (and literals are Unicode in this module)
            env = { b'FOO': b'BAR' }
        c = Command('echo foo', env=env)
        d = c.kwargs['env']
        ek = set(e)
        dk = set(d)
        ek.add('FOO')
        self.assertEqual(dk, ek)
        self.assertEqual(d['FOO'], 'BAR')

    def test_env_usage(self):
        if os.name == 'nt':
            cmd = 'echo %FOO%'
        else:
            cmd = 'echo $FOO'
        if PY3:
            env = {'FOO': 'BAR'}
        else:
            # Python 2.x wants native strings, at least on Windows
            # (and literals are Unicode in this module)
            env = { b'FOO': b'BAR' }
        c = Command(cmd, env=env, stdout=Capture(), shell=True)
        c.run()
        self.assertEqual(c.stdout.text.strip(), 'BAR')

    def test_shlex(self):
        TESTS = (
            ('',
             []),
            ('a',
             [('a', 'a')]),
            ('a && b\n',
             [('a', 'a'), ('&&', 'c'), ('b', 'a')]),
            ('a | b; c>/fred/jim-sheila.txt|&d;e&',
             [('a', 'a'), ('|', 'c'), ('b', 'a'), (';', 'c'), ('c', 'a'),
                 ('>', 'c'), ('/fred/jim-sheila.txt', 'a'), ('|&', 'c'),
                 ('d', 'a'),
                 (';', 'c'), ('e', 'a'), ('&', 'c')])
        )
        for posix in False, True:
            for s, expected in TESTS:
                s = shell_shlex(s, posix=posix, control=True)
                actual = []
                while True:
                    t, tt = s.get_token(), s.token_type
                    if not t:
                        break
                    actual.append((t, tt))
                self.assertEqual(actual, expected)

    def test_shlex_without_control(self):
        TESTS = (
            ('',
             []),
            ('a',
             [('a', 'a')]),
            ('a && b\n',
             [('a', 'a'), ('&', 'a'), ('&', 'a'), ('b', 'a')]),
            ('a | b; c>/fred/jim-sheila.txt|&d;e&',
             [('a', 'a'), ('|', 'a'), ('b', 'a'), (';', 'a'), ('c', 'a'),
                 ('>', 'a'), ('/fred/jim-sheila.txt', 'a'), ('|', 'a'),
                 ('&', 'a'),
                 ('d', 'a'), (';', 'a'), ('e', 'a'), ('&', 'a')])
        )
        for posix in False, True:
            for s, expected in TESTS:
                s = shell_shlex(s, posix=posix)
                actual = []
                while True:
                    t, tt = s.get_token(), s.token_type
                    if not t:
                        break
                    actual.append((t, tt))
                self.assertEqual(actual, expected)

    def test_shlex_with_quoting(self):
        TESTS = (
            ('"a b"', False, [('"a b"', '"')]),
            ('"a b"', True, [('a b', 'a')]),
            ('"a b"  c# comment', False, [('"a b"', '"'), ('c', 'a')]),
            ('"a b"  c# comment', True, [('a b', 'a'), ('c', 'a')]),
        )
        for s, posix, expected in TESTS:
            s = shell_shlex(s, posix=posix)
            actual = []
            while True:
                t, tt = s.get_token(), s.token_type
                if not t:
                    break
                actual.append((t, tt))
            self.assertEqual(actual, expected)
        s = shell_shlex('"abc')
        self.assertRaises(ValueError, s.get_token)

    def test_shlex_with_misc_chars(self):
        TESTS = (
            ('rsync user.name@host.domain.tld:path dest',
             ('rsync', 'user.name@host.domain.tld:path', 'dest')),
            (r'c:\Python26\Python lister.py -d 0.01',
             (r'c:\Python26\Python', 'lister.py', '-d', '0.01')),
        )
        for s, t in TESTS:
            sh = shell_shlex(s)
            self.assertEqual(tuple(sh), t)

    def test_shlex_issue_31(self):
        cmd = "python -c 'print('\''ok'\'')'"
        list(shell_shlex(cmd, control='();>|&', posix=True))
        shell_format("python -c {0}", "print('ok')")
        list(shell_shlex(cmd, control='();>|&', posix=True))

    def test_shlex_issue_34(self):
        cmd = "ls foo,bar"
        actual = list(shell_shlex(cmd))
        self.assertEqual(actual, ['ls', 'foo,bar'])

    def test_parsing(self):
        parse_command_line('abc')
        parse_command_line('abc " " # comment')
        parse_command_line('abc \ "def"')
        parse_command_line('(abc)')
        self.assertRaises(ValueError, parse_command_line, '(abc')
        self.assertRaises(ValueError, parse_command_line, '&&')
        parse_command_line('(abc>def)')
        parse_command_line('(abc 2>&1; def >>&2)')
        parse_command_line('(a|b;c d && e || f >ghi jkl 2> mno)')
        parse_command_line('(abc; (def)); ghi & ((((jkl & mno)))); pqr')

    def test_parsing_special(self):
        for cmd in ('ls -l --color=auto', 'sleep 0.5', 'ls /tmp/abc.def',
                    'ls *.py?', r'c:\Python26\Python lister.py -d 0.01'):
            node = parse_command_line(cmd, posix=False)
            if sys.platform != 'win32':
                self.assertEqual(node.command, cmd.split())
            else:
                split = cmd.split()[1:]
                self.assertEqual(node.command[1:], split)

    def test_parsing_controls(self):
        clp = CommandLineParser()
        gvc = clp.get_valid_controls
        self.assertEqual(gvc('>>>>'), ['>>', '>>'])
        self.assertEqual(gvc('>>'), ['>>'])
        self.assertEqual(gvc('>>>'), ['>>', '>'])
        self.assertEqual(gvc('>>>>>'), ['>>', '>>', '>'])
        self.assertEqual(gvc('))))'), [')', ')', ')', ')'])
        self.assertEqual(gvc('>>;>>'), ['>>', ';', '>>'])
        self.assertEqual(gvc(';'), [';'])
        self.assertEqual(gvc(';;'), [';', ';'])
        self.assertEqual(gvc(');'), [')', ';'])
        self.assertEqual(gvc('>&'), ['>', '&'])
        self.assertEqual(gvc('>>&'), ['>>', '&'])
        self.assertEqual(gvc('||&'), ['||', '&'])
        self.assertEqual(gvc('|&'), ['|&'])

    #def test_scratch(self):
    #    import pdb; pdb.set_trace()
    #    parse_command_line('(a|b;c d && e || f >ghi jkl 2> mno)')

    def test_parsing_errors(self):
        self.assertRaises(ValueError, parse_command_line, '(abc')
        self.assertRaises(ValueError, parse_command_line, '(abc |&| def')
        self.assertRaises(ValueError, parse_command_line, '&&')
        self.assertRaises(ValueError, parse_command_line, 'abc>')
        self.assertRaises(ValueError, parse_command_line, 'a 3> b')
        self.assertRaises(ValueError, parse_command_line, 'abc >&x')
        self.assertRaises(ValueError, parse_command_line, 'a > b | c')
        self.assertRaises(ValueError, parse_command_line, 'a 2> b |& c')
        self.assertRaises(ValueError, parse_command_line, 'a > b > c')
        self.assertRaises(ValueError, parse_command_line, 'a > b >> c')
        self.assertRaises(ValueError, parse_command_line, 'a 2> b 2> c')
        self.assertRaises(ValueError, parse_command_line, 'a 2>> b 2>> c')
        self.assertRaises(ValueError, parse_command_line, 'a 3> b')

    def test_pipeline_no_input_stdout(self):
        with Capture() as out:
            with Pipeline('echo foo 2> %s | cat | cat' % os.devnull,
                          stdout=out) as pl:
                pl.run()
            self.assertEqual(out.read().strip(), b'foo')

    def test_pipeline_no_input_stderr(self):
        if os.name != 'posix':
            raise unittest.SkipTest('This test works only on POSIX')
        with Capture() as err:
            with Pipeline('echo foo 2> %s | cat | cat >&2' % os.devnull,
                          stderr=err) as pl:
                pl.run()
            self.assertEqual(err.read().strip(), b'foo')

    def test_pipeline_no_input_pipe_stderr(self):
        if os.name != 'posix':
            raise unittest.SkipTest('This test works only on POSIX')
        with Capture() as err:
            with Pipeline('echo foo 2> %s | cat >&2 |& cat >&2' %
                          os.devnull, stderr=err) as pl:
                pl.run()
            self.assertEqual(err.read().strip(), b'foo')

    def test_pipeline_with_input_stdout(self):
        logger.debug('starting')
        with Capture() as out:
            with Pipeline('cat 2>> %s | cat | cat' % os.devnull,
                          stdout=out) as pl:
                pl.run(input='foo' * 1000)
            self.assertEqual(out.read().strip(), b'foo' * 1000)

    def test_pipeline_no_input_redirect_stderr(self):
        if os.name != 'posix':
            raise unittest.SkipTest('This test works only on POSIX')
        with Capture() as err:
            with Pipeline('echo foo 2> %s | cat 2>&1 | cat >&2' % os.devnull,
                          stderr=err) as pl:
                pl.run()
            self.assertEqual(err.read().strip(), b'foo')

    def test_pipeline_swap_outputs(self):
        for fn in ('stdout.log', 'stderr.log'):
            if os.path.exists(fn):
                os.unlink(fn)
        with Pipeline('echo foo | tee stdout.log 3>&1 1>&2 2>&3 | '
                      'tee stderr.log > %s' % os.devnull) as pl:
            pl.run()
            with open('stdout.log') as f:
                self.assertEqual(f.read().strip(), 'foo')
            with open('stderr.log') as f:
                self.assertEqual(f.read().strip(), 'foo')
        for fn in ('stdout.log', 'stderr.log'):
            os.unlink(fn)

    def test_pipeline_large_file(self):
        if os.path.exists('dest.bin'):  #pragma: no cover
            os.unlink('dest.bin')
        if not os.path.exists('random.bin'):    #pragma: no cover
            with open('random.bin', 'wb') as f:
                f.write(os.urandom(20 * 1048576))
        with Pipeline('cat random.bin | cat | cat | cat | cat | '
                      'cat > dest.bin ') as pl:
            pl.run()
        with open('random.bin', 'rb') as f:
            data1 = f.read()
        with open('dest.bin', 'rb') as f:
            data2 = f.read()
        os.unlink('dest.bin')
        self.assertEqual(data1, data2)

    def test_logical_and(self):
        with Capture() as out:
            with Pipeline('false && echo foo', stdout=out) as pl:
                pl.run()
        self.assertEqual(out.read().strip(), b'')
        with Capture() as out:
            with Pipeline('true && echo foo', stdout=out) as pl:
                pl.run()
        self.assertEqual(out.read().strip(), b'foo')
        with Capture() as out:
            with Pipeline('false | cat && echo foo', stdout=out) as pl:
                pl.run()
        self.assertEqual(out.read().strip(), b'foo')

    def test_logical_or(self):
        with Capture() as out:
            with Pipeline('false || echo foo', stdout=out) as pl:
                pl.run()
        self.assertEqual(out.read().strip(), b'foo')
        with Capture() as out:
            with Pipeline('true || echo foo', stdout=out) as pl:
                pl.run()
        self.assertEqual(out.read().strip(), b'')

    def test_list(self):
        with Capture() as out:
            with Pipeline('echo foo > %s; echo bar' % os.devnull,
                          stdout=out) as pl:
                pl.run()
        self.assertEqual(out.read().strip(), b'bar')

    def test_list_merge(self):
        with Capture() as out:
            with Pipeline('echo foo; echo bar; echo baz', stdout=out) as pl:
                pl.run()
        self.assertEqual(out.read().split(), [b'foo', b'bar', b'baz'])

    def test_capture_when_other_piped(self):
        with Capture() as out:
            with Pipeline('echo foo; echo bar |& cat', stdout=out) as pl:
                pl.run()
        self.assertEqual(out.read().split(), [b'foo', b'bar'])

    def test_pipeline_func(self):
        self.assertEqual(run('false').returncode, 1)
        with Capture() as out:
            self.assertEqual(run('echo foo', stdout=out).returncode, 0)
        self.assertEqual(out.bytes.strip(), b'foo')

    def test_double_redirect(self):
        with Capture() as out:
            self.assertRaises(ValueError, run, 'echo foo > %s' % os.devnull,
                              stdout=out)
        with Capture() as out:
            with Capture() as err:
                self.assertRaises(ValueError, run,
                                  'echo foo 2> %s' % os.devnull, stdout=out,
                                  stderr=err)

    def test_pipeline_async(self):
        logger.debug('starting')
        with Capture() as out:
            p = run('echo foo & (sleep 2; echo bar) & (sleep 1; echo baz)',
                    stdout=out)
            self.assertEqual(p.returncode, 0)
        items = out.bytes.split()
        for item in (b'foo', b'bar', b'baz'):
            self.assertTrue(item in items)
        self.assertTrue(items.index(b'bar') > items.index(b'baz'))

    def ensure_emitter(self):
        if not os.path.exists('emitter.py'): #pragma: no cover
            with open('emitter.py', 'w') as f:
                f.write(EMITTER)

    def test_capture_stdout(self):
        p = capture_stdout('echo foo')
        self.assertEqual(p.stdout.text.strip(), 'foo')

    def test_get_stdout(self):
        s = get_stdout('echo foo; echo bar')
        self.assertEqual(s.split(), ['foo', 'bar'])

    def test_capture_stderr(self):
        self.ensure_emitter()
        p = capture_stderr('"%s" emitter.py > %s' % (sys.executable,
                                                     os.devnull))
        self.assertEqual(p.stderr.text.strip(), 'bar')

    def test_get_stderr(self):
        self.ensure_emitter()
        s = get_stderr('"%s" emitter.py > %s' % (sys.executable, os.devnull))
        self.assertEqual(s.strip(), 'bar')

    def test_get_both(self):
        self.ensure_emitter()
        t = get_both('"%s" emitter.py' % sys.executable)
        self.assertEqual([s.strip() for s in t], ['foo', 'bar'])

    def test_capture_both(self):
        self.ensure_emitter()
        p = capture_both('"%s" emitter.py' % sys.executable)
        self.assertEqual(p.stdout.text.strip(), 'foo')
        self.assertEqual(p.stderr.text.strip(), 'bar')

    def test_byte_iterator(self):
        p = capture_stdout('echo foo; echo bar')
        lines = []
        for line in p.stdout:
            lines.append(line.strip())
        self.assertEqual(lines, [b'foo', b'bar'])

    def test_text_iterator(self):
        p = capture_stdout('echo foo; echo bar')
        lines = []
        for line in TextIOWrapper(p.stdout):
            lines.append(line)
        self.assertEqual(lines, ['foo\n', 'bar\n'])

    def test_partial_line(self):
        p = capture_stdout('echo foobarbaz')
        lines = [p.stdout.readline(6), p.stdout.readline().strip()]
        self.assertEqual(lines, [b'foobar', b'baz'])

    def test_returncodes(self):
        p = capture_stdout('echo foo; echo bar; echo baz; false')
        self.assertEqual(p.returncodes, [0, 0, 0, 1])
        self.assertEqual(p.returncode, 1)

    def test_processes(self):
        p = capture_stdout('echo foo; echo bar; echo baz; false')
        plist = p.processes
        for p in plist:
            self.assertTrue(isinstance(p, Popen))

    def test_command_run(self):
        c = Command('echo foo'.split(), stdout=Capture())
        c.run()
        self.assertEqual(c.returncode, 0)

    def test_command_nonexistent(self):
        c = Command('nonesuch foo'.split(), stdout=Capture())
        if PY3:
            ARR = self.assertRaisesRegex
        else:
            ARR = self.assertRaisesRegexp
        ARR(ValueError, 'Command not found: nonesuch', c.run)

    def test_working_dir(self):
        d = tempfile.mkdtemp()
        try:
            run('touch newfile.txt', cwd=d)
            files = os.listdir(d)
            self.assertEqual(files, ['newfile.txt'])
        finally:
            shutil.rmtree(d)

    def test_expect(self):
        cap = Capture(buffer_size=-1)   # line buffered
        p = run('%s lister.py -d 0.01' % sys.executable,
                async_=True, stdout=cap)
        timeout = 1.0
        m1 = cap.expect('^line 1\r?$', timeout)
        self.assertTrue(m1)
        m2 = cap.expect('^line 5\r?$', timeout)
        self.assertTrue(m2)
        m3 = cap.expect('^line 1.*\r?$', timeout)
        self.assertTrue(m3)
        cap.close(True)
        p.commands[0].kill()
        data = cap.bytes
        self.assertEqual(data[m1.start():m1.end()].rstrip(), b'line 1')
        self.assertEqual(data[m2.start():m2.end()].rstrip(), b'line 5')
        self.assertEqual(data[m3.start():m3.end()].rstrip(), b'line 10')

    def test_redirection_with_whitespace(self):
        node = parse_command_line('a 2 > b')
        self.assertEqual(node.command, ['a', '2'])
        self.assertEqual(node.redirects, {1: ('>', 'b')})
        node = parse_command_line('a 2> b')
        self.assertEqual(node.command, ['a'])
        self.assertEqual(node.redirects, {2: ('>', 'b')})
        node = parse_command_line('a 2 >> b')
        self.assertEqual(node.command, ['a', '2'])
        self.assertEqual(node.redirects, {1: ('>>', 'b')})
        node = parse_command_line('a 2>> b')
        self.assertEqual(node.command, ['a'])
        self.assertEqual(node.redirects, {2: ('>>', 'b')})

    def test_redirection_with_cwd(self):
        workdir = tempfile.mkdtemp()
        try:
            run('echo hello > world', cwd=workdir)
            p = os.path.join(workdir, 'world')
            self.assertTrue(os.path.exists(p))
            with open(p) as f:
                self.assertEqual(f.read().strip(), 'hello')
        finally:
            shutil.rmtree(workdir)

    if sys.platform == 'win32':  #pragma: no cover

        def test_which_batch(self):
            with open('hellobat.bat', 'w') as f:
                f.write('echo Hello World"')
            with open('hellobat.cmd', 'w') as f:
                f.write('echo Hello World')

            cmd = which('hellobat.bat')
            self.assertIsNotNone(cmd, '.bat not in PATHEXT or not registered')
            self.assertEqual(cmd, '.\\hellobat.bat')
            cmd = which('hellobat.cmd')
            self.assertIsNotNone(cmd, '.cmd not in PATHEXT or not registered')
            self.assertEqual(cmd, '.\\hellobat.cmd')
            cmd = which('hellobat')
            self.assertEqual(cmd, '.\\hellobat.BAT')

            # This might be false on case sensitive file systems
            self.assertTrue(os.path.exists('.\\hellobat.CMD'))

        def _test_which_python(self):
            with open('hellopy.py', 'w') as f:
                f.write('print("Hello, world!")')
            with open('hellopy.pyw', 'w') as f:
                f.write('print("Hello, world!")')

            cmd = which('hellopy.py')
            self.assertIsNotNone(cmd, '.py not in PATHEXT or not registered')
            self.assertEqual(cmd, '.\\hellopy.py')
            cmd = which('hellopy')
            self.assertEqual(cmd, '.\\hellopy.PY')

            # This might be false on case sensitive file systems
            self.assertTrue(os.path.exists('.\\hellopy.PY'))

            cmd = which('hellopy.pyw')
            self.assertIsNotNone(cmd, '.pyw not in PATHEXT or not registered')
            self.assertEqual(cmd, '.\\hellopy.pyw')

        def test_run_which_python_noext(self):
            with open('hellopy.py', 'w') as f:
                f.write('print("Hello, world!")')

            cmd = which('hellopy')
            if not cmd:
                raise unittest.SkipTest('.py not in PATHEXT or not registered')
            self.assertEqual(cmd, '.\\hellopy.PY')

            p = capture_stdout('hellopy')
            self.assertEqual(p.stdout.text.rstrip(), 'Hello, world!')

        def test_run_which_python(self):
            with open('hellopy.py', 'w') as f:
                f.write('print("Hello, world!")')

            cmd = which('hellopy.py')
            if not cmd:
                raise unittest.SkipTest('.py not in PATHEXT or not registered')
            self.assertEqual(cmd, '.\\hellopy.py')

            p = capture_stdout(cmd)
            self.assertEqual(p.stdout.text.rstrip(), 'Hello, world!')

        def test_find_command(self):
            with open('hellopy.py', 'w') as f:
                f.write('print("Hello, world!")')
            with open('hellopy.pyw', 'w') as f:
                f.write('print("Hello, world!")')
            cmd = find_command('hellopy.py')
            self.assertIsNotNone(cmd)
            self.assertTrue(pyrunner_re.match(str(cmd)))
            cmd = find_command('hellopy.pyw')
            self.assertIsNotNone(cmd)
            self.assertTrue(pywrunner_re.match(str(cmd)))

        def test_run_found_command_python(self):
            with open('hellopy.py', 'w') as f:
                f.write('print("Hello, world!")')
            cmd = find_command('hellopy')
            self.assertIsNotNone(cmd)
            p = capture_stdout('hellopy')
            self.assertEqual(p.stdout.text.rstrip(), 'Hello, world!')

        def test_run_found_command_ruby(self):
            with open('hellorb.rb', 'w') as f:
                f.write('puts "Hello, world!"')
            cmd = find_command('hellorb')
            self.assertIsNotNone(cmd)
            p = capture_stdout('hellorb')
            self.assertEqual(p.stdout.text.rstrip(), 'Hello, world!')

        def test_run_found_command_perl(self):
            with open('hellopl.pl', 'w') as f:
                f.write('use 5.010; say "Hello, world!";')
            cmd = find_command('hellopl')
            self.assertIsNotNone(cmd)
            p = capture_stdout('hellopl')
            self.assertEqual(p.stdout.text.rstrip(), 'Hello, world!')

        def test_find_command_hta(self):
            if '.HTA' not in os.environ.get('PATHEXT', '').split(os.path.pathsep):
                raise unittest.SkipTest('.hta not in PATHEXT or not registered')
            with open('hellohta.hta', 'w') as f:
                f.write('<HTA:APPLICATION icon="#" WINDOWSTATE="minimize" '
                        'SHOWINTASKBAR="no" SYSMENU="no" CAPTION="no"/>')
            cmd = find_command('.\\hellohta.hta')
            self.assertEqual(cmd, ['C:\\Windows\\SysWOW64\\mshta.exe', 'hellohta.hta', '{1E460BD7-F1C3-4B2E-88BF-4E770A288AF5}%U{1E460BD7-F1C3-4B2E-88BF-4E770A288AF5}'])

        if os.path.exists('Hello.jar'):
            def test_find_command_jar(self):
                if '.JAR' not in os.environ.get('PATHEXT', '').split(os.path.pathsep):
                    raise unittest.SkipTest('.jar not in PATHEXT or not registered')
                cmd = find_command('Hello.jar')
                self.assertIsNotNone(cmd)
                # Ignore the Java version in the directory
                self.assertTrue(cmd[0].endswith('\\bin\\javaw.exe'))
                # The purpose of these jar tests is to check spaces in cmd
                # If this fails, a new test case needs to be devised.
                self.assertIn(' ', cmd[0])
                self.assertEqual(cmd[1:], ['-jar', 'Hello.jar'])

            def test_run_found_command_jar(self):
                p = capture_stdout('Hello.jar')
                self.assertIsNotNone(p)
                self.assertIn('Hello', p.stdout.text)

        def test_find_command_no_quotes(self):
            if '.BLG' not in os.environ.get('PATHEXT', '').split(os.path.pathsep):
                raise unittest.SkipTest('.blg not in PATHEXT or not registered')
            key = winreg.OpenKey(HKCR, 'Diagnostic.Perfmon.Document\\shell\\open\\command')
            s, _ = winreg.QueryValueEx(key, None)
            # The purpose of this tests is to check no quotes and args.
            # If this fails, a new test subject needs to be used.
            self.assertEqual(s, '%SystemRoot%\\system32\\perfmon /sys /open "%1"')

            with open('helloblg.blg', 'w') as f:
                f.write('')
            cmd = find_command('helloblg.blg')
            self.assertIsNotNone(cmd)
            self.assertEqual(cmd[0], '%SystemRoot%\\system32\\perfmon')
            self.assertEqual(cmd[1:], ['/sys', '/open', 'helloblg.blg'])

        if os.environ.get('WINDOWS_SDK_VERSION', None):
            # Tests including spaces in the file path
            def test_find_command_msvc_setenv(self):
                version = os.environ.get('WINDOWS_SDK_VERSION')
                cmd = find_command('setenv')
                self.assertIsNotNone(cmd)
                self.assertEqual(cmd, (None, 'C:\\Program Files\\Microsoft SDKs\\Windows\\' + version + '\\Bin\\setenv.CMD'))

            def test_run_found_command_msvc_setenv(self):
                p = capture_stdout('SetEnv')
                self.assertIsNotNone(p)
                self.assertIn('Setting SDK environment', p.stdout.text)

            def test_find_command_msvc_msi(self):
                # The bin directory contains three; MsiVal2.Msi, Orca.Msi and
                # DeviceSimulatorForWindowsSideShow.msi
                if '.MSI' not in os.environ.get('PATHEXT', '').split(os.path.pathsep):
                    raise unittest.SkipTest('.msi not in PATHEXT or not registered')
                version = os.environ.get('WINDOWS_SDK_VERSION')
                msi_file = 'C:\\Program Files\\Microsoft SDKs\\Windows\\' + version + '\\Bin\\MsiVal2.Msi'
                cmd = find_command(msi_file)
                self.assertIsNotNone(cmd)
                self.assertEqual(cmd, ['%SystemRoot%\System32\msiexec.exe', '/i', msi_file])

        if 'C:\\Ruby24' in os.environ['PATH']:
            def test_find_command_ruby_gem(self):
                cmd = find_command('gem')
                self.assertEqual(cmd, (None, 'C:\\Ruby24\\bin\\gem.BAT'))

            def test_find_command_ruby_rdoc(self):
                cmd = find_command('rdoc')
                self.assertEqual(cmd, (None, 'C:\\Ruby24\\bin\\rdoc.CMD'))

            def test_find_command_ruby_bundler(self):
                cmd = find_command('bundler')
                self.assertEqual(cmd, (None, 'C:\\Ruby24\\bin\\bundler.BAT'))

            def test_run_found_command_ruby_gem(self):
                p = capture_stdout('gem list -l rdoc')
                self.assertIsNotNone(p)
                self.assertIn('rdoc', p.stdout.text)
                self.assertIn('default:', p.stdout.text)

            def test_run_found_command_ruby_rdoc(self):
                p = capture_stdout('rdoc C:\\Ruby24\\bin\\rdoc')
                self.assertIsNotNone(p)
                self.assertIn('rdoc', p.stdout.text)
                self.assertIn('100%', p.stdout.text)

            def test_run_found_command_ruby_bundler(self):
                p = capture_stdout('bundler help')
                self.assertIsNotNone(p)
                self.assertIn('Ruby Dependency Management', p.stdout.text)

        def test_find_command_all_extensions(self):
            from sarge.utils import COMMAND_RE, EXECUTABLE_EXTENSIONS
            # Failures here are be due to bugs in the host registry
            # https://github.com/appveyor/ci/issues/2955
            IGNORE_EXTENSIONS = (
                '.dtproj', '.ispac', '.tt',
                '.SSISDeploymentManifest', '.vdp', '.vdproj',
                '.wpa',
                '.fs', '.fsscript', '.fsx',  '.fsi', '.fsproj',
            )
            def iter_extns():
                hdl = winreg.ConnectRegistry(None, HKCR)
                try:
                    i = 0
                    while True:
                        subkey = winreg.EnumKey(hdl, i)
                        if subkey.startswith('.'):
                            yield subkey
                        #else:
                        #    print('discarding', subkey)
                        i += 1

                except WindowsError as e:
                    print(i, e)
                    # WindowsError: [Errno 259] No more data is available
                    pass

            count = 0
            NO_EXE = []
            for extn in iter_extns():
                if extn in IGNORE_EXTENSIONS:
                    continue
                if extn != extn.lower():
                    print('{} is not lowercase'.format(extn))

                ftype = winreg.QueryValue(HKCR, extn.lower())

                if not ftype:
                    # Check that OpenKey does work
                    try:
                        key = winreg.OpenKey(HKCR, extn)
                        print('succeeded openkey extn {}'.format(extn))
                    except OSError:
                        raise RuntimeError('failed openkey extn {}'.format(extn))
                    if extn != extn.lower():
                        try:
                            key = winreg.OpenKey(HKCR, extn.lower())
                            print('succeeded openkey lowercase extn {}'.format(extn))
                        except OSError:
                            raise RuntimeError('openkey extn {} is not found at {}'.format(extn, extn.lower()))
                    print('ftype for extn {} is missing'.format(extn))
                    continue
                if ' ' in ftype:
                    print('spaces in {} ftype {}'.format(extn, ftype))
                if ftype.startswith('.'):
                    print('{} ftype {} starts with a dot'.format(extn, ftype))
                path = os.path.join(ftype, 'shell', 'open', 'command')
                try:
                    key = winreg.OpenKey(HKCR, path)
                except OSError:
                    try:
                        key = winreg.OpenKey(HKCR, ftype)
                    except OSError:
                        print('ftype is missing', extn, ftype)
                        continue
                    print('openkey failed', extn, path)
                    continue
                try:
                    exe, _ = winreg.QueryValueEx(key, None)
                except OSError:
                    print('Unexpectedly missing value', extn, path)
                    continue
                if not exe:
                    if ftype in ('AnalysisServices.BIMFile', 'AnalysisServices.BISMProject'):
                        continue
                    raise RuntimeError('Unexpectedly empty value', path)
                elif '%' not in exe:
                    # Doesnt contain %0, %1 or %L, so not really an open command
                    continue
                elif '%0' not in exe and '%1' not in exe and '%L' not in exe and '%l' not in exe:
                    # Doesnt contain %0, %1 or %L, so not really an open command
                    continue
                if exe in ('"%1" %*', '%1 %*', '"%1" /S'):
                    print('execute the file', extn, ftype, exe)
                    NO_EXE.append(extn)
                    continue
                if not COMMAND_RE.match(exe):
                    raise RuntimeError('skipping unmatched {} {} {}'.format(extn, ftype, exe))
                    continue

                exe_expanded = winreg.ExpandEnvironmentStrings(exe)
                print(extn, ftype, exe, os.path.exists(exe), exe_expanded, os.path.exists(exe_expanded))
                count = count + 1
                try:
                    cmd = find_command('.\\foo' + extn)
                except RuntimeError as e:
                    print(extn, e)
                    raise
                self.assertIsNotNone(cmd)
                if not cmd[0]:
                    self.assertIn(cmd[1], ['foo' + extn, '.\\foo' + extn.upper()])
                elif cmd[0] in ('iexplore', 'iexplore.exe'):
                    # iexplore is executable even when not in the PATH
                    continue
                else:
                    expanded = winreg.ExpandEnvironmentStrings(cmd[0])
                    if '%' in expanded:
                        # .tt VisualStudio.TextTemplating.12.0 %VsInstallDir%\devenv.exe
                        raise ValueError('Expansions of {} didnt work'.format(cmd[0]))

                    # print('expanded', expanded)
                    if not os.path.exists(expanded):
                        if os.path.isabs(expanded):
                            if not expanded.endswith('.exe'):
                                expanded = expanded + '.exe'
                        else:
                            expanded = which(expanded)
                        # print('expanded', expanded)

                    self.assertTrue(
                        os.path.exists(expanded),
                        '{}: {} expanded to {} not found'.format(
                            extn, cmd, expanded))

            # arbitary check to ensure some processing occurred
            self.assertGreater(count, 100)
            self.assertEqual(set(NO_EXE), set(EXECUTABLE_EXTENSIONS))
            self.assertTrue(False)

    def test_feeder(self):
        feeder = Feeder()
        p = capture_stdout([sys.executable, 'echoer.py'], input=feeder,
                           async_=True)
        try:
            lines = ('hello', 'goodbye')
            gen = iter(lines)
            # p.commands may not be set yet (separate thread)
            while not p.commands or p.commands[0].returncode is None:
                logger.debug('commands: %s', p.commands)
                try:
                    data = next(gen)
                except StopIteration:
                    break
                feeder.feed(data + '\n')
                if p.commands:
                    p.commands[0].poll()
                time.sleep(0.05)    # wait for child to return echo
        finally:
            # p.commands may not be set yet (separate thread)
            if p.commands:
                p.commands[0].terminate()
            feeder.close()
        self.assertEqual(p.stdout.text.splitlines(),
                         ['hello hello', 'goodbye goodbye'])


if __name__ == '__main__':  #pragma: no cover
    # switch the level to DEBUG for in-depth logging.
    fn = 'test_sarge-%d.%d.log' % sys.version_info[:2]
    logging.basicConfig(level=logging.DEBUG, filename=fn, filemode='w',
                        format='%(threadName)s %(funcName)s %(lineno)d '
                               '%(message)s')
    logging.getLogger('sarge.parse').setLevel(logging.WARNING)
    fn = 'threads-%d.%d.log' % sys.version_info[:2]
    if TRACE_THREADS:
        start_trace(fn)
    try:
        unittest.main()
    finally:
        if TRACE_THREADS:
            stop_trace()
