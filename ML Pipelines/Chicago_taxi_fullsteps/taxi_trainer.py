# Trainer component copied COLAB practice
from typing import List, Text
import os, datetime, taxi_constants
from absl import logging

import tensorflow as tf
import tensorflow_transform as tft
from tfx import v1 as tfx
from tfx_bsl.public import tfxio

#--------------------------------


_DENSE_FLOAT_FEATURE_KEYS = taxi_constants.DENSE_FLOAT_FEATURE_KEYS
_VOCAB_FEATURE_KEYS = taxi_constants.VOCAB_FEATURE_KEYS
_VOCAB_SIZE = taxi_constants.VOCAB_SIZE
_OOV_SIZE = taxi_constants.OOV_SIZE
_FEATURE_BUCKET_COUNT = taxi_constants.FEATURE_BUCKET_COUNT
_BUCKET_FEATURE_KEYS = taxi_constants.BUCKET_FEATURE_KEYS
_CATEGORICAL_FEATURE_KEYS = taxi_constants.CATEGORICAL_FEATURE_KEYS
_MAX_CATEGORICAL_FEATURE_VALUES = taxi_constants.MAX_CATEGORICAL_FEATURE_VALUES
_LABEL_KEY = taxi_constants.LABEL_KEY

def _get_tf_examples_serving_signature(model, tf_transform_output):
  """Returns a serving signature that accepts `tensorflow.Example`."""

  # We need to track the layers in the model in order to save it.
  # TODO(b/162357359): Revise once the bug is resolved.
  model.tft_layer_inference = tf_transform_output.transform_features_layer()

  @tf.function(input_signature=[
      tf.TensorSpec(shape=[None], dtype=tf.string, name='examples')
  ])
  def serve_tf_examples_fn(serialized_tf_example):
    """Returns the output to be used in the serving signature."""
    raw_feature_spec = tf_transform_output.raw_feature_spec()
    # Remove label feature since these will not be present at serving time.
    raw_feature_spec.pop(_LABEL_KEY)
    raw_features = tf.io.parse_example(serialized_tf_example, raw_feature_spec)
    transformed_features = model.tft_layer_inference(raw_features)
    logging.info('serve_transformed_features = %s', transformed_features)

    outputs = model(transformed_features)
    # TODO(b/154085620): Convert the predicted labels from the model using a
    # reverse-lookup (opposite of transform.py).
    return {'outputs': outputs}

  return serve_tf_examples_fn


def _get_transform_features_signature(model, tf_transform_output):
  """Returns a serving signature that applies tf.Transform to features."""

  # We need to track the layers in the model in order to save it.
  # TODO(b/162357359): Revise once the bug is resolved.
  model.tft_layer_eval = tf_transform_output.transform_features_layer()

  @tf.function(input_signature=[
      tf.TensorSpec(shape=[None], dtype=tf.string, name='examples')
  ])
  def transform_features_fn(serialized_tf_example):
    """Returns the transformed_features to be fed as input to evaluator."""
    raw_feature_spec = tf_transform_output.raw_feature_spec()
    raw_features = tf.io.parse_example(serialized_tf_example, raw_feature_spec)
    transformed_features = model.tft_layer_eval(raw_features)
    logging.info('eval_transformed_features = %s', transformed_features)
    return transformed_features

  return transform_features_fn


def _input_fn(file_pattern: List[Text],
              data_accessor: tfx.components.DataAccessor,
              tf_transform_output: tft.TFTransformOutput,
              batch_size: int = 200) -> tf.data.Dataset:
  """Generates features and label for tuning/training.

  Args:
    file_pattern: List of paths or patterns of input tfrecord files.
    data_accessor: DataAccessor for converting input to RecordBatch.
    tf_transform_output: A TFTransformOutput.
    batch_size: representing the number of consecutive elements of returned
      dataset to combine in a single batch

  Returns:
    A dataset that contains (features, indices) tuple where features is a
      dictionary of Tensors, and indices is a single Tensor of label indices.
  """
  return data_accessor.tf_dataset_factory(
      file_pattern,
      tfxio.TensorFlowDatasetOptions(
          batch_size=batch_size, label_key=_LABEL_KEY),
      tf_transform_output.transformed_metadata.schema)


def _build_keras_model(hidden_units: List[int] = None) -> tf.keras.Model:
  """Creates a DNN Keras model for classifying taxi data.

  Args:
    hidden_units: [int], the layer sizes of the DNN (input layer first).

  Returns:
    A keras Model.
  """
  real_valued_columns = [
      tf.feature_column.numeric_column(key, shape=())
      for key in _DENSE_FLOAT_FEATURE_KEYS
  ]
  categorical_columns = [
      tf.feature_column.categorical_column_with_identity(
          key, num_buckets=_VOCAB_SIZE + _OOV_SIZE, default_value=0)
      for key in _VOCAB_FEATURE_KEYS
  ]
  categorical_columns += [
      tf.feature_column.categorical_column_with_identity(
          key, num_buckets=_FEATURE_BUCKET_COUNT, default_value=0)
      for key in _BUCKET_FEATURE_KEYS
  ]
  categorical_columns += [
      tf.feature_column.categorical_column_with_identity(  # pylint: disable=g-complex-comprehension
          key,
          num_buckets=num_buckets,
          default_value=0) for key, num_buckets in zip(
              _CATEGORICAL_FEATURE_KEYS,
              _MAX_CATEGORICAL_FEATURE_VALUES)
  ]
  indicator_column = [
      tf.feature_column.indicator_column(categorical_column)
      for categorical_column in categorical_columns
  ]

  model = _wide_and_deep_classifier(
      # TODO(b/139668410) replace with premade wide_and_deep keras model
      wide_columns=indicator_column,
      deep_columns=real_valued_columns,
      dnn_hidden_units=hidden_units or [100, 70, 50, 25])
  return model


def _wide_and_deep_classifier(wide_columns, deep_columns, dnn_hidden_units):
  """Build a simple keras wide and deep model.

  Args:
    wide_columns: Feature columns wrapped in indicator_column for wide (linear)
      part of the model.
    deep_columns: Feature columns for deep part of the model.
    dnn_hidden_units: [int], the layer sizes of the hidden DNN.

  Returns:
    A Wide and Deep Keras model
  """
  # Following values are hard coded for simplicity in this example,
  # However prefarably they should be passsed in as hparams.

  # Keras needs the feature definitions at compile time.
  # TODO(b/139081439): Automate generation of input layers from FeatureColumn.
  input_layers = {
      colname: tf.keras.layers.Input(name=colname, shape=(), dtype=tf.float32)
      for colname in _DENSE_FLOAT_FEATURE_KEYS
  }
  input_layers.update({
      colname: tf.keras.layers.Input(name=colname, shape=(), dtype='int32')
      for colname in _VOCAB_FEATURE_KEYS
  })
  input_layers.update({
      colname: tf.keras.layers.Input(name=colname, shape=(), dtype='int32')
      for colname in _BUCKET_FEATURE_KEYS
  })
  input_layers.update({
      colname: tf.keras.layers.Input(name=colname, shape=(), dtype='int32')
      for colname in _CATEGORICAL_FEATURE_KEYS
  })

  # TODO(b/161952382): Replace with Keras preprocessing layers.
  deep = tf.keras.layers.DenseFeatures(deep_columns)(input_layers)
  for numnodes in dnn_hidden_units:
    deep = tf.keras.layers.Dense(numnodes)(deep)
  wide = tf.keras.layers.DenseFeatures(wide_columns)(input_layers)

  output = tf.keras.layers.Dense(1)(
          tf.keras.layers.concatenate([deep, wide]))

  model = tf.keras.Model(input_layers, output)
  model.compile(
      loss=tf.keras.losses.BinaryCrossentropy(from_logits=True),
      optimizer=tf.keras.optimizers.Adam(lr=0.001),
      metrics=[tf.keras.metrics.BinaryAccuracy()])
  model.summary(print_fn=logging.info)
  return model


# TFX Trainer will call this function.
def run_fn(fn_args: tfx.components.FnArgs):
  """Train the model based on given args.

  Args:
    fn_args: Holds args used to train the model as name/value pairs.
  """
  # Number of nodes in the first layer of the DNN
  first_dnn_layer_size = 100
  num_dnn_layers = 4
  dnn_decay_factor = 0.7

  tf_transform_output = tft.TFTransformOutput(fn_args.transform_output)

  train_dataset = _input_fn(fn_args.train_files, fn_args.data_accessor, 
                            tf_transform_output, 40)
  eval_dataset = _input_fn(fn_args.eval_files, fn_args.data_accessor, 
                           tf_transform_output, 40)

  model = _build_keras_model(
      # Construct layers sizes with exponetial decay
      hidden_units=[
          max(2, int(first_dnn_layer_size * dnn_decay_factor**i))
          for i in range(num_dnn_layers)
      ])

  tensorboard_callback = tf.keras.callbacks.TensorBoard(
      log_dir=fn_args.model_run_dir, update_freq='batch')
  model.fit(
      train_dataset,
      steps_per_epoch=fn_args.train_steps,
      validation_data=eval_dataset,
      validation_steps=fn_args.eval_steps,
      callbacks=[tensorboard_callback])

  signatures = {
      'serving_default':
          _get_tf_examples_serving_signature(model, tf_transform_output),
      'transform_features':
          _get_transform_features_signature(model, tf_transform_output),
  }
  model.save(fn_args.serving_model_dir, save_format='tf', signatures=signatures)
