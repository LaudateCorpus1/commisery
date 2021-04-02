# Copyright (c) 2019 - 2021 TomTom N.V.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections import OrderedDict
import json
import os
import re
import subprocess
import sys
from textwrap import dedent

import pytest

try:
    # Python >= 3.8
    from importlib import metadata
except ImportError:
    import importlib_metadata as metadata


try:
    _hopic_version = tuple(
            (int(x) if re.match('^[0-9]+$', x) else x)
            for x in metadata.version('hopic').split('.')
        )
    hopic_cli = [ep for ep in metadata.entry_points()['console_scripts'] if ep.name == 'hopic'][0].load()
    from click.testing import CliRunner
    import git
except metadata.PackageNotFoundError:
    _hopic_version = ()

_git_time = f"{7 * 24 * 3600} +0000"


def run_with_config(config, *args, files={}, env=None, cfg_file='hopic-ci-config.yaml'):
    runner = CliRunner(mix_stderr=False, env=env)
    with runner.isolated_filesystem():
        with git.Repo.init() as repo:
            if '/' in cfg_file and not os.path.exists(os.path.dirname(cfg_file)):
                os.makedirs(os.path.dirname(cfg_file))
            with open(cfg_file, 'w') as f:
                f.write(config)
            for fname, content in files.items():
                if '/' in fname and not os.path.exists(os.path.dirname(fname)):
                    os.makedirs(os.path.dirname(fname))
                with open(fname, 'w') as f:
                    f.write(content)
            repo.index.add((cfg_file,) + tuple(files.keys()))
            repo.index.commit(message='Initial commit', author_date=_git_time, commit_date=_git_time)

        for arg in args:
            if cfg_file != 'hopic-ci-config.yaml':
                arg = ('--config', cfg_file, *arg)
            result = runner.invoke(hopic_cli, arg)

            if result.stdout_bytes:
                print(result.stdout, end='')
            if result.stderr_bytes:
                print(result.stderr, end='', file=sys.stderr)

            if result.exception is not None and not isinstance(result.exception, SystemExit):
                raise result.exception

            yield result

            if result.exit_code != 0:
                return


@pytest.mark.skipif(_hopic_version < (1,36), reason="Hopic >= 1.36.0 not available")
def test_commisery_template(capfd):
    (result,) = run_with_config(dedent('''\
                phases:
                  style:
                    commit-messages: !template "commisery"
                '''), ('show-config',))

    assert result.exit_code == 0
    output = json.loads(result.stdout, object_pairs_hook=OrderedDict)
    expanded = output['phases']['style']['commit-messages']
    assert expanded[0]['image'] is None
    commits, head = [e['sh'] for e in expanded]
    assert 'commisery.checking' in commits
    assert head[-2:] == ["commisery.checking", "HEAD"]


@pytest.mark.parametrize('ticket', [True, False])
@pytest.mark.skipif(_hopic_version < (1,36), reason="Hopic >= 1.36.0 not available")
def test_commisery_template_range(capfd, monkeypatch, ticket):
    import hopic.build

    class MockGitInfo():
        source_commit = "OUR_SOURCE_COMMIT"
        target_commit = 'OUR_TARGET_COMMIT'
        submit_commit = "OUR_MERGE_COMMIT"
        submit_ref = "master"
        autosquashed_commit = "OUR_AUTOSQUASHED_COMMIT_1"
        autosquashed_commits = [autosquashed_commit, 'OUR_AUTOSQUASHED_COMMIT_2']

        @classmethod
        def from_repo(cls, *args):
            return cls()

    expected_commit_ranges = [
        "OUR_TARGET_COMMIT..OUR_AUTOSQUASHED_COMMIT_1",
        "HEAD",
    ]
    def mock_check_call(args, *popenargs, **kwargs):
        expected = expected_commit_ranges.pop(0)
        assert args[-1] == expected

    monkeypatch.setattr(hopic.build.HopicGitInfo, "from_repo", MockGitInfo.from_repo)
    monkeypatch.setattr(hopic.build, 'HopicGitInfo', MockGitInfo)
    monkeypatch.setattr(subprocess, "check_call", mock_check_call)

    cfg_result, build_result = run_with_config(dedent(f'''\
                phases:
                  style:
                    commit-messages: !template
                      name: commisery
                      require-ticket: {ticket}
                '''),
        ("show-config",),
        ("build",),
    )

    assert build_result.exit_code == 0
    output = json.loads(cfg_result.stdout, object_pairs_hook=OrderedDict)
    expanded = output['phases']['style']['commit-messages']
    assert expanded[0]['image'] is None
    commit_range, head = [e['sh'] for e in expanded]
    assert ('--ticket' in commit_range) == ticket
    assert commit_range[-1:] == ["${AUTOSQUASHED_COMMITS}"]
    assert "commisery.checking" in commit_range
    assert head[-2:] == ["commisery.checking", "HEAD"]
