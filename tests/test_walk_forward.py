import unittest

from offline.backtests.walk_forward import walk_forward_splits


class WalkForwardSplitsTest(unittest.TestCase):
    def test_rolling_windows_are_strictly_temporal(self):
        folds = walk_forward_splits(
            20, train_size=8, validation_size=3, test_size=2, step_size=2,
        )
        self.assertEqual(len(folds), 4)
        self.assertEqual(folds[0].train, slice(0, 8))
        self.assertEqual(folds[0].validation, slice(8, 11))
        self.assertEqual(folds[0].test, slice(11, 13))
        self.assertEqual(folds[1].train, slice(2, 10))

        for fold in folds:
            self.assertEqual(fold.train.stop, fold.validation.start)
            self.assertEqual(fold.validation.stop, fold.test.start)
            self.assertLessEqual(fold.test.stop, 20)

    def test_anchored_train_expands_without_leaking_future_data(self):
        folds = walk_forward_splits(
            20, train_size=8, validation_size=3, test_size=2,
            step_size=2, anchored_train=True,
        )
        self.assertEqual(folds[0].train, slice(0, 8))
        self.assertEqual(folds[1].train, slice(0, 10))
        self.assertEqual(folds[1].validation, slice(10, 13))
        self.assertEqual(folds[1].test, slice(13, 15))

    def test_insufficient_history_returns_no_fold(self):
        self.assertEqual(
            walk_forward_splits(12, train_size=8, validation_size=3, test_size=2),
            [],
        )

    def test_invalid_sizes_fail_fast(self):
        with self.assertRaises(ValueError):
            walk_forward_splits(20, train_size=0, validation_size=3, test_size=2)
        with self.assertRaises(ValueError):
            walk_forward_splits(
                20, train_size=8, validation_size=3, test_size=2, step_size=-1,
            )


if __name__ == "__main__":
    unittest.main()
