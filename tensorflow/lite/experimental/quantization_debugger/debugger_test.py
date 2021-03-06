# Copyright 2021 The TensorFlow Authors. All Rights Reserved.
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
# ==============================================================================
"""Tests for QuantizationDebugger."""

import numpy as np
import tensorflow as tf

from tensorflow.lite.experimental.quantization_debugger import debugger
from tensorflow.lite.python import convert
from tensorflow.lite.python import lite
from tensorflow.python.framework import test_util
from tensorflow.python.platform import test
from tensorflow.python.training.tracking import tracking


def _get_model():
  """Returns somple model with Conv2D and representative dataset gen."""
  root = tracking.AutoTrackable()
  kernel_in = np.array([-2, -1, 1, 2], dtype=np.float32).reshape((2, 2, 1, 1))

  @tf.function(
      input_signature=[tf.TensorSpec(shape=[1, 3, 3, 1], dtype=tf.float32)])
  def func(inp):
    kernel = tf.constant(kernel_in, dtype=tf.float32)
    conv = tf.nn.conv2d(inp, kernel, strides=1, padding='SAME')
    output = tf.nn.relu(conv, name='output')
    return output

  root.f = func
  to_save = root.f.get_concrete_function()
  return to_save


def _calibration_gen():
  for i in range(5):
    yield [np.arange(9).reshape((1, 3, 3, 1)).astype(np.float32) * i]


def _quantize_model(func, calibration_gen, debug=True):
  """Quantizes model, in debug or normal mode."""
  converter = lite.TFLiteConverterV2.from_concrete_functions([func])
  converter.target_spec.supported_ops = [lite.OpsSet.TFLITE_BUILTINS_INT8]
  converter.representative_dataset = calibration_gen

  # Create a TFLite model with new quantizer and numeric verify ops.
  converter.optimizations = [lite.Optimize.DEFAULT]
  converter.experimental_new_quantizer = True
  if debug:
    converter._experimental_calibrate_only = True
    calibrated = converter.convert()
    return convert.mlir_quantize(calibrated, enable_numeric_verify=True)
  else:
    return converter.convert()


class QuantizationDebuggerTest(test_util.TensorFlowTestCase):

  @classmethod
  def setUpClass(cls):
    super().setUpClass()
    cls.float_model = _get_model()
    cls.debug_model = _quantize_model(cls.float_model, _calibration_gen)

  @test_util.run_v2_only
  def test_quantization_debugger(self):
    options = debugger.QuantizationDebugOptions(
        layer_debug_metrics={'l1_norm': lambda diffs: np.mean(np.abs(diffs))})
    quant_debugger = debugger.QuantizationDebugger(
        quant_debug_model_content=QuantizationDebuggerTest.debug_model,
        debug_dataset=_calibration_gen,
        debug_options=options)
    quant_debugger.run()

    expected_metrics = {
        'num_elements': 9,
        'stddev': 0.03850026,
        'mean_error': 0.01673192,
        'max_abs_error': 0.10039272,
        'mean_square_error': 0.0027558778,
        'l1_norm': 0.023704167,
    }
    self.assertLen(quant_debugger.layer_statistics, 1)
    actual_metrics = next(iter(quant_debugger.layer_statistics.values()))

    self.assertCountEqual(expected_metrics.keys(), actual_metrics.keys())
    for key, value in expected_metrics.items():
      self.assertAlmostEqual(value, actual_metrics[key], places=5)

  @test_util.run_v2_only
  def test_quantization_debugger_wrong_input_raises_ValueError(self):

    def wrong_calibration_gen():
      for _ in range(5):
        yield [
            np.ones((1, 3, 3, 1), dtype=np.float32),
            np.ones((1, 3, 3, 1), dtype=np.float32)
        ]

    quant_debugger = debugger.QuantizationDebugger(
        quant_debug_model_content=QuantizationDebuggerTest.debug_model,
        debug_dataset=wrong_calibration_gen)
    with self.assertRaisesRegex(
        ValueError, r'inputs provided \(2\).+inputs to the model \(1\)'):
      quant_debugger.run()

  @test_util.run_v2_only
  def test_quantization_debugger_non_debug_model_raises_ValueError(self):
    normal_quant_model = _quantize_model(
        QuantizationDebuggerTest.float_model, _calibration_gen, debug=False)

    with self.assertRaisesRegex(
        ValueError, 'Please check if the quantized model is in debug mode'):
      debugger.QuantizationDebugger(
          quant_debug_model_content=normal_quant_model,
          debug_dataset=_calibration_gen)


if __name__ == '__main__':
  test.main()
