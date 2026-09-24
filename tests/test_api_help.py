"""Public help is complete, readable, offline, and independent of negotiation."""
import json

import pytest

from qmt_rpyc.cli.client import main
from qmt_rpyc.cli.api_help import describe_api
from qmt_rpyc.contracts.documentation import OPERATION_DESCRIPTIONS
from qmt_rpyc.contracts.operations import CONTRACT_HASH, OPERATIONS


def test_all_operations_and_nested_fields_are_documented():
    assert set(OPERATION_DESCRIPTIONS) == set(OPERATIONS)
    def check(node):
        if not isinstance(node, dict):
            return
        for item in node.get('fields', {}).values():
            assert item['description'].strip()
            check(item['type'])
        for arg in node.get('args', []):
            check(arg)
    for name in OPERATIONS:
        api = describe_api(name)
        for key in ('request', 'response', 'error'):
            check(api[key])
    assert CONTRACT_HASH == 'ce5ba31ec0d948354590569d375b99d474fe60c045130f5875ac151674010a4c'


def test_list_is_one_description_per_operation_without_schemas(capsys):
    assert main(['api', 'list']) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(OPERATIONS)
    assert all(line.split()[0] in OPERATIONS for line in lines)
    assert 'CodesRequest' not in '\n'.join(lines)


def test_describe_shows_types_defaults_and_nested_return_fields(capsys):
    assert main(['api', 'describe', 'market.get_daily_bars']) == 0
    text = capsys.readouterr().out
    for expected in ('请求参数', '返回结果', 'codes:', '最多 500', '必填', '默认 null',
                     'DailyBar', 'trade_date:', '上海交易日', 'Failure', 'OperationError'):
        assert expected in text


@pytest.mark.parametrize('args', [
    ['api', '--json', 'describe', 'options.get_option_chain'],
    ['api', 'describe', 'options.get_option_chain', '--json'],
    ['api', 'describe', 'options.get_option_chain', '--compact'],
])
def test_json_remains_machine_readable(args, capsys):
    assert main(args) == 0
    value = json.loads(capsys.readouterr().out)
    assert value['request']['fields']['expiry_date']['description']
    assert value['response']['fields']['contract_codes']['description']


def test_invalid_group_or_operation_fails(capsys):
    assert main(['api', 'list', 'does_not_exist']) == 2
    assert main(['api', 'describe', 'does_not_exist']) == 2
