# coding=utf-8
# Copyright 2020 The Mesh TensorFlow Authors.
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

"""Implementation of various types of attention."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import mesh_tensorflow as mtf

import tensorflow.compat.v1 as tf


def attention(q,
              k,
              v,
              memory_length_dim,
              key_dim,
              value_dim,
              bias=None,
              dropout_rate=0.0,
              dropout_broadcast_dims=None,
              extra_logit=None):
  """Dot-product attention - doesn't use positional dimensions.

  key_dim is a Dimension representing the channels in the queries and keys
  value_dim is a Dimension representing the channels in values
  memory_length_dim is a Dimension representing the different key/value pairs.

  Dimensions of q: other_query_dims + {key_dim}
  Dimensions of k: other_memory_dims + {memory_length_dim, key_dim}
  Dimensions of v: other_memory_dims + {memory_length_dim, value_dim}
  other_memory_dims is a subset of other_query_dims

  Typically, other_query_dims={batch, heads, length}
  Typically, other_memory_dims={batch, heads}

  Args:
    q: a Tensor
    k: a Tensor
    v: a Tensor
    memory_length_dim: a Dimension
    key_dim: a Dimension
    value_dim: a Dimension
    bias: a Tensor to be added into the attention logits.
    dropout_rate: a float.
    dropout_broadcast_dims: an optional list of mtf.Dimension
    extra_logit: an optional scalar or tensor

  Returns:
    Tensor with shape q.shape - key_dim + value_dim
  """
  logits = mtf.layers.us_einsum([q, k], reduced_dims=[key_dim])
  if bias is not None:
    logits += bias
  weights = mtf.softmax(logits, memory_length_dim, extra_logit=extra_logit)
  if dropout_rate != 0.0:
    weights = mtf.dropout(
        weights, 1.0 - dropout_rate,
        noise_shape=weights.shape - dropout_broadcast_dims)
  outputs_shape = q.shape - key_dim + value_dim
  outputs = mtf.einsum([weights, v], outputs_shape)
  return outputs


def hybrid_attention(q,
                     k,
                     v,
                     context,
                     memory_length_dim,
                     key_dim,
                     value_dim,
                     bias=None,
                     dropout_rate=0.0,
                     dropout_broadcast_dims=None,
                     extra_logit=None):
  """Dot-product attention - doesn't use positional dimensions.

  key_dim is a Dimension representing the channels in the queries and keys
  value_dim is a Dimension representing the channels in values
  memory_length_dim is a Dimension representing the different key/value pairs.

  Dimensions of q: other_query_dims + {key_dim}
  Dimensions of k: other_memory_dims + {memory_length_dim, key_dim}
  Dimensions of v: other_memory_dims + {memory_length_dim, value_dim}
  other_memory_dims is a subset of other_query_dims

  Typically, other_query_dims={batch, heads, length}
  Typically, other_memory_dims={batch, heads}

  Args:
    q: a Tensor
    k: a Tensor
    v: a Tensor
    context: context of the attention layer.
    memory_length_dim: a Dimension
    key_dim: a Dimension
    value_dim: a Dimension
    bias: a Tensor to be added into the attention logits.
    dropout_rate: a float.
    dropout_broadcast_dims: an optional list of mtf.Dimension
    extra_logit: an optional scalar or tensor

  Returns:
    Tensor with shape q.shape - key_dim + value_dim
  """
  logits = mtf.layers.us_einsum([q, k], reduced_dims=[key_dim])
  if bias is not None:
    logits += bias

  query_length_dim = mtf.Dimension("length", memory_length_dim.size)
  doubly_coeff = mtf.get_variable(
      context.mesh, "doubly_coeff", [],
      initializer=tf.constant_initializer(0.5),
      dtype=context.variable_dtype)
  doubly_coeff = mtf.maximum(mtf.minimum(doubly_coeff, 1.), 0.)

  upper_weights = mtf.softmax(
      logits, memory_length_dim, extra_logit=extra_logit)

  lower_log_weights = mtf.log_softmax(
      logits, query_length_dim, extra_logit=extra_logit)
  doubly_weights = mtf.softmax(
      lower_log_weights, memory_length_dim, extra_logit=extra_logit)

  weights = doubly_coeff * doubly_weights + (1. - doubly_coeff) * upper_weights
  if dropout_rate != 0.0:
    weights = mtf.dropout(
        weights, 1.0 - dropout_rate,
        noise_shape=weights.shape - dropout_broadcast_dims)
  outputs_shape = q.shape - key_dim + value_dim
  outputs = mtf.einsum([weights, v], outputs_shape)
  return outputs


class AttentionParams(object):
  """A set of parameters used for (multihead) attention."""

  def __init__(self,
               mesh,
               query_input_dim,
               memory_input_dim,
               output_dim,
               key_dim,
               value_dim,
               query_heads_dims,
               memory_heads_dims,
               variable_dtype,
               shared_kv=False,
               combine_dims=True,
               ensemble_dim=None,
               keep_query_heads_dims=False):
    """Create attention parameters.

    combine_dims is a hack for faster execution.  The heads and key/value
    dimensions are combined in the variables and the computation.  The hack
    would not be necessary if XLA optimized einsum properly.

    Args:
      mesh: a Mesh
      query_input_dim: a Dimension
      memory_input_dim: a Dimension
      output_dim: a Dimension
      key_dim: a Dimension
      value_dim: a Dimension
      query_heads_dims: a list of Dimension
      memory_heads_dims: a list of Dimension
      variable_dtype: a mtf.VariableDType
      shared_kv: a boolean
      combine_dims: a boolean
      ensemble_dim: an optional Dimension
      keep_query_heads_dims: a boolean, if true keep the query_heads_dims in the
        output.
    """
    if shared_kv and key_dim != value_dim:
      raise ValueError("shared_kv requires key_dim == value_dim")
    self.query_input_dim = query_input_dim
    self.memory_input_dim = memory_input_dim
    self.output_dim = output_dim
    self.key_dim = key_dim
    self.value_dim = value_dim
    self.query_heads_dims = query_heads_dims or []
    self.memory_heads_dims = memory_heads_dims or []
    self.shared_kv = shared_kv
    self.combine_dims = combine_dims
    self.keep_query_heads_dims = keep_query_heads_dims
    if combine_dims:
      q_shape = [query_input_dim, _combined_dim(self.q_dims)]
      k_shape = [memory_input_dim, _combined_dim(self.k_dims)]
      v_shape = [memory_input_dim, _combined_dim(self.v_dims)]
      o_shape = [_combined_dim(self.o_dims), output_dim]
    else:
      q_shape = [query_input_dim] + self.q_dims
      k_shape = [memory_input_dim] + self.k_dims
      v_shape = [memory_input_dim] + self.v_dims
      o_shape = self.o_dims + [output_dim]
    if mtf.layers.unit_scaling_convention():
      init = tf.random_normal_initializer(stddev=1.0)
      q_init = init
      kv_init = init
      o_init = init
    else:
      q_init = tf.random_normal_initializer(
          stddev=(query_input_dim.size * key_dim.size) ** -0.5)
      kv_init = tf.random_normal_initializer(
          stddev=memory_input_dim.size ** -0.5)
      o_init = tf.random_normal_initializer(
          stddev=mtf.Shape(self.query_heads_dims + [value_dim]).size ** -0.5)
    if ensemble_dim:
      q_shape = [ensemble_dim] + q_shape
      k_shape = [ensemble_dim] + k_shape
      v_shape = [ensemble_dim] + v_shape
      o_shape = [ensemble_dim] + o_shape
    self.wq = mtf.get_variable(
        mesh, "q", q_shape, initializer=q_init, dtype=variable_dtype)
    if shared_kv:
      self.wkv = mtf.get_variable(
          mesh, "kv", k_shape, initializer=kv_init, dtype=variable_dtype)
    else:
      self.wk = mtf.get_variable(
          mesh, "k", k_shape, initializer=kv_init, dtype=variable_dtype)
      self.wv = mtf.get_variable(
          mesh, "v", v_shape, initializer=kv_init, dtype=variable_dtype)
    self.wo = mtf.get_variable(
        mesh, "o", o_shape, initializer=o_init, dtype=variable_dtype)

  def compute_q(self, query_antecedent):
    """Compute query Tensor q.

    Args:
      query_antecedent: a Tensor with dimensions
         {query_input_dim} + other_dims
    Returns:
      a Tensor with dimensions
         query_heads_dims + {key_dim} + other_dims
    """
    ret = mtf.layers.us_einsum(
        [query_antecedent, self.wq], reduced_dims=[self.query_input_dim])
    if self.combine_dims:
      ret = mtf.replace_dimensions(ret, ret.shape.dims[-1], self.q_dims)
    return ret

  def compute_kv(self, memory_antecedent):
    """Compute key/value Tensor kv.

    Args:
      memory_antecedent: a Tensor with dimensions
        {memory_input_dim} + other_dims
    Returns:
      a Tensor with dimensions
        memory_heads_dims + {key_dim} + other_dims
    """
    if not self.shared_kv:
      raise ValueError("compute_kv can only be called with shared_kv")
    ret = mtf.layers.us_einsum(
        [memory_antecedent, self.wkv], reduced_dims=[self.memory_input_dim])
    if self.combine_dims:
      ret = mtf.replace_dimensions(ret, ret.shape.dims[-1], self.k_dims)
    return ret

  def compute_k(self, memory_antecedent):
    """Compute key Tensor k.

    Args:
      memory_antecedent: a Tensor with dimensions
        {memory_input_dim} + other_dims
    Returns:
      a Tensor with dimensions
        memory_heads_dims + {key_dim} + other_dims
    """
    if self.shared_kv:
      raise ValueError("compute_k cannot be called with shared_kv")
    ret = mtf.layers.us_einsum(
        [memory_antecedent, self.wk], reduced_dims=[self.memory_input_dim])
    if self.combine_dims:
      ret = mtf.replace_dimensions(ret, ret.shape.dims[-1], self.k_dims)
    return ret

  def compute_v(self, memory_antecedent):
    """Compute value Tensor v.

    Args:
      memory_antecedent: a Tensor with dimensions
        {memory_input_dim} + other_dims
    Returns:
      a Tensor with dimensions
        memory_heads_dims + {value_dim} + other_dims
    """
    if self.shared_kv:
      raise ValueError("compute_v cannot be called with shared_kv")
    ret = mtf.layers.us_einsum(
        [memory_antecedent, self.wv], reduced_dims=[self.memory_input_dim])
    if self.combine_dims:
      ret = mtf.replace_dimensions(ret, ret.shape.dims[-1], self.v_dims)
    return ret

  def compute_output(self, o, output_shape=None):
    """Compute output of multihead attention.

    Args:
      o: a Tensor with dimensions
         query_heads_dims + {value_dim} + other_dims
      output_shape: an optional Shape
    Returns:
      a Tensor with shape:
         {output_dim} + other_dims
    """
    if self.combine_dims:
      o = mtf.transpose(o, o.shape - self.o_dims + self.o_dims)
      o = mtf.replace_dimensions(o, self.o_dims, self.wo.shape.dims[-2])
      reduced_dims = [self.wo.shape.dims[-2]]
    else:
      reduced_dims = self.o_dims

    if self.keep_query_heads_dims:
      reduced_dims = [self.value_dim]

    return mtf.layers.us_einsum(
        [o, self.wo], output_shape=output_shape, reduced_dims=reduced_dims)

  @property
  def q_dims(self):
    return self.query_heads_dims + [self.key_dim]

  @property
  def k_dims(self):
    return self.memory_heads_dims + [self.key_dim]

  @property
  def v_dims(self):
    return self.memory_heads_dims + [self.value_dim]

  @property
  def o_dims(self):
    return self.query_heads_dims + [self.value_dim]


def _combined_dim(dims):
  return mtf.Dimension(dims[0].name, mtf.Shape(dims).size)


def attention_params_simple(
    mesh, io_dim, kv_dim, heads_dim, variable_dtype):
  """Common case attention parameters.

  Args:
    mesh: a Mesh
    io_dim: a Dimension (channels dimension of inputs and outputs)
    kv_dim: a Dimension (channels in keys and values)
    heads_dim: a Dimension (number of attention "heads")
    variable_dtype: a mtf.VariableDType
  Returns:
    an AttentionParams
  """
  return AttentionParams(
      mesh,
      query_input_dim=io_dim,
      memory_input_dim=io_dim,
      output_dim=io_dim,
      key_dim=kv_dim,
      value_dim=kv_dim,
      query_heads_dims=[heads_dim],
      memory_heads_dims=[heads_dim],
      variable_dtype=variable_dtype)


def local_attention_1d(q,
                       k,
                       v,
                       length_dim,
                       key_dim,
                       value_dim,
                       fully_autoregressive=True,
                       length_dim_num_splits=1,
                       radius=128,
                       sequence_id=1,
                       write_priority=None,
                       read_priority=None,
                       attention_kwargs=None):
  """Attention to the a neighborood around the source.

  If fully_autoregressive, then query position p can only see memory positions
  in the range (p - radius, p].

  If not fully_autoregressive, then query position p can only see memory
  positions in the range (p - window_size, p + radius].

  In addition, if write_priority and read_priority are provided, then attention
  is limited to position pairs where
  read_priority[query position] >= write_priority[memory position]

  Args:
    q: a Tensor containing length_dim
    k: a Tensor containing length_dim
    v: an optional Tensor containing length_dim.  If none then uses v=k.
    length_dim: a Dimension
    key_dim: a Dimension (the channels dimension of q and k)
    value_dim: a Dimension (the channels dimension of v)
    fully_autoregressive: a boolean
    length_dim_num_splits: an optional integer indicating how many ways the
      length dimension is split
    radius: an integer
    sequence_id: a Tensor or an integer
    write_priority: an optional Tensor containing length_dim
    read_priority: an optional Tensor containing length_dim
    attention_kwargs: optional keyword arguments for attention()

  Returns:
    a Tensor with the shape x.shape - key_dim + value_dim

  Raises:
    ValueError: if channels or depth don't match.
  """
  # Choose a suitable block size.
  # We choose the greatest divisor of length_per_split less than or equal
  # to max(window_size, 128)
  length_per_split = length_dim.size // length_dim_num_splits
  block_length = max(radius, 128)
  while length_per_split % block_length != 0:
    block_length -= 1
  query_block_length = mtf.Dimension("query_block_length", block_length)
  memory_block_length = mtf.Dimension("memory_block_length", block_length)
  # The num_blocks dimension gets the same name as the length dimension,
  # so it will be split in the same way.
  num_blocks = mtf.Dimension(length_dim.name, length_dim.size // block_length)
  def _reshape_query(x):
    return mtf.replace_dimensions(
        x, length_dim, [num_blocks, query_block_length])
  def _reshape_memory(x):
    x = mtf.replace_dimensions(
        x, length_dim, [num_blocks, memory_block_length])
    return (mtf.left_halo_exchange if fully_autoregressive
            else mtf.halo_exchange)(
                x, num_blocks, memory_block_length, radius)
  q = _reshape_query(q)
  k = _reshape_memory(k)
  if v:
    v = _reshape_memory(v)
  else:
    v = k
  if sequence_id is None:
    sequence_id = 1
  if (not isinstance(sequence_id, mtf.Tensor) or
      length_dim not in sequence_id.shape.dims):
    sequence_id += mtf.zeros(q.mesh, [length_dim], tf.int32)
  q_sequence_id = _reshape_query(sequence_id)
  m_sequence_id = _reshape_memory(sequence_id)
  pos = mtf.range(q.mesh, length_dim, dtype=tf.int32)
  q_pos = _reshape_query(pos)
  m_pos = _reshape_memory(pos)

  padded_memory_block_length = mtf.Dimension(
      "memory_block_length",
      (1 if fully_autoregressive else 2) * radius + block_length)

  relative_position = m_pos - q_pos
  visible = mtf.equal(q_sequence_id, m_sequence_id)
  visible = mtf.logical_and(visible, mtf.greater(relative_position, -radius))
  visible = mtf.logical_and(visible, mtf.less_equal(
      relative_position, 0 if fully_autoregressive else radius))
  if read_priority is not None:
    write_priority = _reshape_memory(write_priority)
    read_priority = _reshape_query(read_priority)
    visible = mtf.logical_and(
        visible, mtf.greater_equal(read_priority, write_priority))

  bias = visibility_mask_to_attention_bias(visible, q.dtype)
  o = attention(q, k, v, padded_memory_block_length,
                key_dim, value_dim, bias, **attention_kwargs)
  return mtf.replace_dimensions(o, [num_blocks, query_block_length], length_dim)


def visibility_mask_to_attention_bias(visible, dtype):
  """Convert a boolean visibility mask to an attention bias.

  The returned Tensor has large negative values in positions where
  visible=False.

  Args:
    visible: a boolean Tensor
    dtype: a dtype
  Returns:
    a Tensor with the given dtype and the same shape as "visible"
  """
  return mtf.cast(mtf.logical_not(visible), dtype) * -1e9
