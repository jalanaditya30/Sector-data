"""Offline regressions for the September 2026 Actions failures."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'early'))
import behavioral_lab as lab


class BehavioralSnapshotTests(unittest.TestCase):
    def test_snapshot_serializes_real_metrics_and_classification(self):
        close = np.linspace(100, 115, 180)
        volume = np.full(180, 100_000.0)
        volume[-10:] *= 1.8
        frame = pd.DataFrame({'Close': close, 'High': close + .1,
                              'Low': close - 1, 'Volume': volume})
        frames = {ticker: frame for ticker in ['TEST.NS', lab.MID, lab.SMALL]}
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(lab, 'HERE', tmp), \
             patch.object(lab.registry, 'read_universe', return_value=['TEST.NS']), \
             patch.object(lab.registry, 'load', return_value={}), \
             patch.object(lab.registry, 'by_yahoo', return_value={}), \
             patch.object(lab, 'fetch', return_value=frames), \
             contextlib.redirect_stdout(io.StringIO()):
            lab.main()
            data = json.loads((Path(tmp) / 'behavioral.json').read_text())
        self.assertEqual(data['resolved'], 1)
        self.assertEqual(data['market_regime'], 'bull')
        row = data['rows'][0]
        self.assertEqual(row['behavior_stage'], 'Recognition')
        self.assertTrue(all(type(value) is bool for value in row['evidence'].values()))
        self.assertEqual(row['evidence_count'], 7)


class ConcurrentPushTests(unittest.TestCase):
    def git(self, directory, *args):
        return subprocess.run(['git', '-C', str(directory), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.remote = self.base / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--initial-branch=main', str(self.remote)],
                       check=True, capture_output=True)
        self.a, self.b = self.base / 'a', self.base / 'b'
        self.git(self.base, 'clone', str(self.remote), str(self.a))
        self.configure(self.a)
        self.commit(self.a, 'data.json', 'original')
        self.git(self.a, 'push', 'origin', 'main')
        self.git(self.base, 'clone', str(self.remote), str(self.b))
        self.configure(self.b)

    def configure(self, repo):
        self.git(repo, 'config', 'user.name', 'Regression Test')
        self.git(repo, 'config', 'user.email', 'test@example.invalid')

    def commit(self, repo, filename, content):
        (repo / filename).write_text(content)
        self.git(repo, 'add', filename)
        self.git(repo, 'commit', '-m', 'Update ' + filename)

    def publish(self):
        return subprocess.run(['bash', str(ROOT / 'scripts/push-generated.sh')],
                              cwd=self.a, env={**os.environ, 'GITHUB_REF_NAME': 'main'},
                              capture_output=True, text=True)

    def test_other_workflow_commit_is_preserved(self):
        self.commit(self.a, 'data.json', 'fresh heatmap')
        self.commit(self.b, 'trend.json', 'fresh trend')
        self.git(self.b, 'push', 'origin', 'main')
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.remote, 'show', 'main:data.json'), 'fresh heatmap')
        self.assertEqual(self.git(self.remote, 'show', 'main:trend.json'), 'fresh trend')
        self.assertEqual(self.publish().returncode, 0)  # no changes is harmless

    def test_conflicting_output_fails_without_overwriting_remote(self):
        self.commit(self.a, 'data.json', 'local output')
        self.commit(self.b, 'data.json', 'new remote output')
        self.git(self.b, 'push', 'origin', 'main')
        original_head = self.git(self.remote, 'rev-parse', 'main')
        result = self.publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), original_head)
        self.assertEqual((self.a / 'data.json').read_text(), 'local output')
        self.assertFalse((self.a / '.git/rebase-merge').exists())


if __name__ == '__main__':
    unittest.main()
