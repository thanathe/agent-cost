import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import capture


def claude_turn(tool_name):
    """One minimal Claude-style turn: a prompt, one tool_use, usage, a reply."""
    return [
        {'type': 'user', 'timestamp': '2026-09-18T10:00:00+07:00',
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': 'do something'}]}},
        {'type': 'assistant', 'timestamp': '2026-09-18T10:00:05+07:00',
         'message': {'id': 'm1', 'role': 'assistant', 'model': 'claude-test',
                     'content': [{'type': 'tool_use', 'id': 't1', 'name': tool_name, 'input': {}}],
                     'usage': {'input_tokens': 100, 'output_tokens': 20}}},
        {'type': 'user', 'timestamp': '2026-09-18T10:00:10+07:00',
         'message': {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}]}},
        {'type': 'assistant', 'timestamp': '2026-09-18T10:00:15+07:00',
         'message': {'id': 'm2', 'role': 'assistant', 'model': 'claude-test',
                     'content': [{'type': 'text', 'text': 'done'}],
                     'usage': {'input_tokens': 0, 'output_tokens': 5}}},
    ]


def codebuddy_turn(tool_name):
    """One minimal CodeBuddy-style turn using function_call events."""
    return [
        {'type': 'message', 'role': 'user', 'timestamp': '2026-09-18T10:00:00+07:00',
         'content': [{'type': 'input_text', 'text': 'do something'}]},
        {'type': 'function_call', 'timestamp': '2026-09-18T10:00:05+07:00',
         'name': tool_name, 'arguments': '{}'},
        {'type': 'function_call_result', 'timestamp': '2026-09-18T10:00:10+07:00', 'result': {}},
        {'type': 'message', 'role': 'assistant', 'timestamp': '2026-09-18T10:00:15+07:00',
         'content': [{'type': 'text', 'text': 'done'}],
         'providerData': {'rawUsage': {'credit': 1.5, 'prompt_tokens': 90, 'completion_tokens': 10},
                          'model': 'glm-test'}},
    ]


def build(events, tpath):
    prompts = capture.human_prompt_indices(events)
    return capture.build_record(events, prompts, prompts[0], tpath, 'sess', '/tmp/repo',
                                '2026-09-18T10:00:20+07:00')


class TranscriptCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tpath(self, agent):
        name = 'claude' if agent == 'claude' else 'codebuddy'
        p = Path(self.tmp.name) / f'.{name}' / 'proj' / 'sess.jsonl'
        p.parent.mkdir(parents=True)
        p.write_text('\n'.join(json.dumps(e) for e in [{}]) + '\n')
        return str(p)


class SerenaDetectionTests(TranscriptCase):
    def test_claude_serena_tool_use_counted(self):
        rec = build(claude_turn('mcp__serena__find_file'), self.tpath('claude'))
        self.assertEqual(rec['n_serena_calls'], 1)
        self.assertEqual(rec['serena_tools'], ['mcp__serena__find_file'])
        self.assertEqual(rec['n_tool_calls'], 1)

    def test_claude_custom_server_name_still_matches(self):
        rec = build(claude_turn('mcp__serena_ide__get_current_config'), self.tpath('claude'))
        self.assertEqual(rec['n_serena_calls'], 1)
        self.assertEqual(rec['serena_tools'], ['mcp__serena_ide__get_current_config'])

    def test_claude_non_serena_tool_not_counted(self):
        rec = build(claude_turn('Read'), self.tpath('claude'))
        self.assertEqual(rec['n_serena_calls'], 0)
        self.assertEqual(rec['serena_tools'], [])

    def test_codebuddy_function_call_serena_counted(self):
        rec = build(codebuddy_turn('serena__execute_shell_command'), self.tpath('codebuddy'))
        self.assertEqual(rec['n_serena_calls'], 1)
        self.assertEqual(rec['serena_tools'], ['serena__execute_shell_command'])

    def test_codebuddy_non_serena_not_counted_and_credit_kept(self):
        rec = build(codebuddy_turn('Bash'), self.tpath('codebuddy'))
        self.assertEqual(rec['n_serena_calls'], 0)
        self.assertEqual(rec['credit'], 1.5)

    def test_duplicate_tool_use_blocks_counted_once(self):
        events = claude_turn('mcp__serena__find_file')
        # Claude repeats content blocks across events — dedupe via tool_use id stays at 1.
        dup = dict(events[1])
        dup['message'] = dict(events[1]['message'])
        events.insert(2, dup)
        rec = build(events, self.tpath('claude'))
        self.assertEqual(rec['n_serena_calls'], 1)
        self.assertEqual(rec['serena_tool_counts'], {'find_file': 1})


class SerenaDetailTests(TranscriptCase):
    def test_claude_per_tool_counts_use_short_name(self):
        events = claude_turn('mcp__serena__find_symbol')
        events[1]['message']['content'].append(
            {'type': 'tool_use', 'id': 't2', 'name': 'mcp__serena__find_symbol', 'input': {}})
        events[1]['message']['content'].append(
            {'type': 'tool_use', 'id': 't3', 'name': 'mcp__serena__replace_symbol_body', 'input': {}})
        rec = build(events, self.tpath('claude'))
        self.assertEqual(rec['serena_tool_counts'], {'find_symbol': 2, 'replace_symbol_body': 1})
        self.assertEqual(rec['n_serena_calls'], 3)

    def test_claude_serena_error_matched_by_tool_use_id(self):
        events = claude_turn('mcp__serena__find_symbol')
        events[2]['message']['content'][0]['is_error'] = True
        rec = build(events, self.tpath('claude'))
        self.assertEqual(rec['n_serena_errors'], 1)
        self.assertEqual(rec['n_tool_errors'], 1)

    def test_claude_non_serena_error_not_counted_as_serena(self):
        events = claude_turn('Read')
        events[2]['message']['content'][0]['is_error'] = True
        rec = build(events, self.tpath('claude'))
        self.assertEqual(rec['n_serena_errors'], 0)

    def test_claude_serena_relative_path_recorded(self):
        events = claude_turn('mcp__serena__get_symbols_overview')
        events[1]['message']['content'][0]['input'] = {'relative_path': 'src/app.js'}
        rec = build(events, self.tpath('claude'))
        self.assertEqual(rec['serena_files'], ['src/app.js'])

    def test_codebuddy_serena_error_by_status_and_call_id(self):
        events = codebuddy_turn('serena__find_symbol')
        events[1]['callId'] = 'c1'
        events[1]['arguments'] = json.dumps({'relative_path': 'lib/x.py'})
        events[2].update({'callId': 'c1', 'name': 'serena__find_symbol', 'status': 'error'})
        rec = build(events, self.tpath('codebuddy'))
        self.assertEqual(rec['serena_tool_counts'], {'find_symbol': 1})
        self.assertEqual(rec['n_serena_errors'], 1)
        self.assertEqual(rec['serena_files'], ['lib/x.py'])

    def test_codebuddy_serena_error_matched_by_call_id_only(self):
        events = codebuddy_turn('serena__find_symbol')
        events[1]['callId'] = 'c1'
        events[2].update({'callId': 'c1', 'status': 'failed'})
        rec = build(events, self.tpath('codebuddy'))
        self.assertEqual(rec['n_serena_errors'], 1)

    def test_codebuddy_serena_error_matched_by_name_only(self):
        events = codebuddy_turn('serena__find_symbol')
        events[2].update({'name': 'serena__find_symbol', 'status': 'error'})
        rec = build(events, self.tpath('codebuddy'))
        self.assertEqual(rec['n_serena_errors'], 1)

    def test_codebuddy_non_serena_failure_not_counted_as_serena(self):
        events = codebuddy_turn('Bash')
        events[1]['callId'] = 'c9'
        events[2].update({'callId': 'c9', 'name': 'Bash', 'status': 'error'})
        rec = build(events, self.tpath('codebuddy'))
        self.assertEqual(rec['n_serena_errors'], 0)
        self.assertEqual(rec['n_tool_errors'], 1)

    def test_serena_errors_never_exceed_tool_errors(self):
        events = codebuddy_turn('serena__find_symbol')
        events[2].update({'name': 'serena__find_symbol', 'status': 'error'})
        rec = build(events, self.tpath('codebuddy'))
        self.assertLessEqual(rec['n_serena_errors'], rec['n_tool_errors'])

    def test_codebuddy_completed_status_is_not_error(self):
        events = codebuddy_turn('serena__find_symbol')
        events[2].update({'name': 'serena__find_symbol', 'status': 'completed'})
        rec = build(events, self.tpath('codebuddy'))
        self.assertEqual(rec['n_serena_errors'], 0)

    def test_no_serena_gives_empty_detail(self):
        rec = build(claude_turn('Bash'), self.tpath('claude'))
        self.assertEqual(rec['serena_tool_counts'], {})
        self.assertEqual(rec['n_serena_errors'], 0)
        self.assertEqual(rec['serena_files'], [])


if __name__ == '__main__':
    unittest.main()
