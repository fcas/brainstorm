#!/usr/bin/env python
# coding=utf-8
from __future__ import division, print_function, unicode_literals

from __future__ import division, print_function, unicode_literals
from collections import OrderedDict
from brainstorm.layers.base_layer import LayerBaseImpl
from brainstorm.utils import LayerValidationError
from brainstorm.structure.shapes import ShapeTemplate


class BinomialCrossEntropyLayerImpl(LayerBaseImpl):
    """
    Calculate the Binomial Cross Entropy between outputs and **binary** targets

    Cross entropy is by definition asymmetric, therefore the inputs are named
    'default' for the network outputs and 'targets' for the binary targets.
    This layer only calculates the deltas for the default inputs.
    Also note that this implementation only works for **binary** targets and
    outputs in the range 0 to 1.
    For outputs outside that range or non-binary targets the result is
    undefined.
    """

    inputs = {'default': ShapeTemplate('T', 'B', '...'),
              'targets': ShapeTemplate('T', 'B', '...')}

    outputs = {'default': ShapeTemplate('T', 'B', 1)}

    expected_kwargs = {}

    def _get_output_shapes(self):
        return {'default': ShapeTemplate('T', 'B', 1)}

    def get_internal_structure(self):
        feature_shape = self.in_shapes['default'].feature_shape
        internals = OrderedDict()
        internals['cee'] = ShapeTemplate('T', 'B', *feature_shape)
        return internals

    def _validate_in_shapes(self):
        super(BinomialCrossEntropyLayerImpl, self)._validate_in_shapes()

        if self.in_shapes['default'] != self.in_shapes['targets']:
            raise LayerValidationError("{}: default and targets must have the "
                                       "same shapes but got {} and {}"
                                       .format(self.name,
                                               self.in_shapes['default'],
                                               self.in_shapes['targets']))

    def forward_pass(self, forward_buffers, training_pass=True):
        # prepare
        _h = self.handler
        y = forward_buffers.inputs.default
        t = forward_buffers.inputs.targets
        cee = forward_buffers.internals.cee
        cee_sum = forward_buffers.outputs.default

        # the binomial cross entropy error is given by
        # - t * ln(y) - (1-t) * ln(1-y)
        tmp = _h.ones(cee.shape)
        _h.subtract_tt(tmp, y, cee)     # cee = 1-y
        _h.subtract_tt(tmp, t, tmp)     # tmp  = 1-t
        _h.clip_t(cee, 1e-6, 1.0, cee)
        _h.log_t(cee, cee)              # cee = ln(1-y)
        _h.elem_mult_tt(tmp, cee, tmp)  # tmp = (1-t) * ln(1-y)

        _h.clip_t(y, 1e-6, 1.0, cee)
        _h.log_t(cee, cee)              # cee = ln(y)
        _h.elem_mult_tt(t, cee, cee)    # cee = t * ln(y)

        _h.add_tt(tmp, cee, cee)        # cee = (1-t) * ln(1-y) + t * ln(y)

        # reshape for summation
        t, b = cee.shape[:2]
        f = _h.size(cee) / (t * b)
        cee = cee.reshape((t, b, f))

        _h.sum_t(cee, axis=2, out=cee_sum)
        _h.elem_mult_st(-1, cee_sum, cee_sum)  # * -1

    def backward_pass(self, forward_buffers, backward_buffers):
        # prepare
        _h = self.handler
        ceed_sum = backward_buffers.outputs.default
        ceed = backward_buffers.internals.cee
        tmp = _h.allocate(ceed.shape)

        y = forward_buffers.inputs.default
        t = forward_buffers.inputs.targets

        yd = backward_buffers.inputs.default

        # the derivative of the binomial cross entropy error is given by
        # (y - t) / (y - y²)

        _h.elem_mult_tt(y, y, ceed)       # ceed = y²
        _h.subtract_tt(y, ceed, ceed)     # ceed = y - y²
        _h.clip_t(ceed, 1e-6, 1.0, ceed)  # clip

        _h.subtract_tt(y, t, tmp)         # tmp = y - t

        _h.divide_tt(tmp, ceed, ceed)     # ceed = (y - t) / (y - y²)

        # ceed_sum has only one feature dimension due to summation,
        # so we broadcast to all feature dimensions
        _h.broadcast_features_t(ceed_sum, tmp)
        _h.elem_mult_tt(ceed, tmp, ceed)

        _h.add_tt(ceed, yd, yd)