#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright (c) 2019 Huawei Technologies Co., Ltd.
# A-Tune is licensed under the Mulan PSL v2.
# You can use this software according to the terms and conditions of the Mulan PSL v2.
# You may obtain a copy of Mulan PSL v2 at:
#     http://license.coscl.org.cn/MulanPSL2
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY OR FIT FOR A PARTICULAR
# PURPOSE.
# See the Mulan PSL v2 for more details.
# Create: 2019-10-29

"""
This class is used to find optimal settings and generate optimized profile.
"""

import logging
import numbers
import multiprocessing
import collections
import numpy as np
import analysis.engine.utils.utils as utils
from sklearn.linear_model import Lasso
from sklearn.preprocessing import StandardScaler

from skopt import Optimizer as baseOpt
from skopt.utils import normalize_dimensions
from skopt.utils import cook_estimator

from analysis.optimizer.abtest_tuning_manager import ABtestTuningManager
from analysis.optimizer.knob_sampling_manager import KnobSamplingManager
from analysis.optimizer.tpe_optimizer import TPEOptimizer
from analysis.optimizer.weighted_ensemble_feature_selector import WeightedEnsembleFeatureSelector

LOGGER = logging.getLogger(__name__)


class Optimizer(multiprocessing.Process):
    """find optimal settings and generate optimized profile"""

    def __init__(self, name, params, child_conn, prj_name, engine="bayes", max_eval=50, sel_feature=False,
                 x0=None, y0=None, n_random_starts=20, split_count=5, noise=0.00001 ** 2):
        super(Optimizer, self).__init__(name=name)
        self.knobs = params
        self.child_conn = child_conn
        self.project_name = prj_name
        self.engine = engine
        self.noise = noise
        self.max_eval = int(max_eval)
        self.split_count = split_count
        self.sel_feature = sel_feature
        self.x_ref = x0
        self.y_ref = y0
        if self.x_ref is not None and len(self.x_ref) == 1:
            ref_x, _ = self.transfer()
            self.ref = ref_x[0]
        else:
            self.ref = []
        self._n_random_starts = 20 if n_random_starts is None else n_random_starts

    def build_space(self):
        """build space"""
        objective_params_list = []
        for i, p_nob in enumerate(self.knobs):
            if p_nob['type'] == 'discrete':
                items = self.handle_discrete_data(p_nob, i)
                objective_params_list.append(items)
            elif p_nob['type'] == 'continuous':
                r_range = p_nob['range']
                if r_range is None or len(r_range) != 2:
                    raise ValueError("the item of the scope value of {} must be 2"
                                     .format(p_nob['name']))
                if p_nob['dtype'] == 'int':
                    try:
                        r_range[0] = int(r_range[0])
                        r_range[1] = int(r_range[1])
                    except ValueError:
                        raise ValueError("the range value of {} is not an integer value"
                                         .format(p_nob['name']))
                elif p_nob['dtype'] == 'float':
                    try:
                        r_range[0] = float(r_range[0])
                        r_range[1] = float(r_range[1])
                    except ValueError:
                        raise ValueError("the range value of {} is not an float value"
                                         .format(p_nob['name']))

                if len(self.ref) > 0:
                    if self.ref[i] < r_range[0] or self.ref[i] > r_range[1]:
                        raise ValueError("the ref value of {} is out of range"
                                         .format(p_nob['name']))
                objective_params_list.append((r_range[0], r_range[1]))
            else:
                raise ValueError("the type of {} is not supported".format(p_nob['name']))
        return objective_params_list

    def handle_discrete_data(self, p_nob, index):
        """handle discrete data"""
        if p_nob['dtype'] == 'int':
            items = p_nob['items']
            if items is None:
                items = []
            r_range = p_nob['range']
            step = 1
            if 'step' in p_nob.keys():
                step = 1 if p_nob['step'] < 1 else p_nob['step']
            if r_range is not None:
                length = len(r_range) if len(r_range) % 2 == 0 else len(r_range) - 1
                for i in range(0, length, 2):
                    items.extend(list(np.arange(r_range[i], r_range[i + 1] + 1, step=step)))
            items = list(set(items))
            if len(self.ref) > 0:
                try:
                    ref_value = int(self.ref[index])
                except ValueError:
                    raise ValueError("the ref value of {} is not an integer value"
                                     .format(p_nob['name']))
                if ref_value not in items:
                    items.append(ref_value)
            return items
        if p_nob['dtype'] == 'float':
            items = p_nob['items']
            if items is None:
                items = []
            r_range = p_nob['range']
            step = 0.1
            if 'step' in p_nob.keys():
                step = 0.1 if p_nob['step'] <= 0 else p_nob['step']
            if r_range is not None:
                length = len(r_range) if len(r_range) % 2 == 0 else len(r_range) - 1
                for i in range(0, length, 2):
                    items.extend(list(np.arange(r_range[i], r_range[i + 1], step=step)))
            items = list(set(items))
            if len(self.ref) > 0:
                try:
                    ref_value = float(self.ref[index])
                except ValueError:
                    raise ValueError("the ref value of {} is not a float value"
                                     .format(p_nob['name']))
                if ref_value not in items:
                    items.append(ref_value)
            return items
        if p_nob['dtype'] == 'string':
            items = p_nob['options']
            if len(self.ref) > 0:
                try:
                    ref_value = str(self.ref[index])
                except ValueError:
                    raise ValueError("the ref value of {} is not a string value"
                                     .format(p_nob['name']))
                if ref_value not in items:
                    items.append(ref_value)
            return items
        raise ValueError("the dtype of {} is not supported".format(p_nob['name']))

    @staticmethod
    def feature_importance(options, performance, labels):
        """feature importance"""
        options = StandardScaler().fit_transform(options)
        lasso = Lasso()
        lasso.fit(options, performance)
        result = zip(lasso.coef_, labels)
        total_sum = sum(map(abs, lasso.coef_))
        if total_sum == 0:
            return ", ".join("%s: 0" % label for label in labels)
        result = sorted(result, key=lambda x: -np.abs(x[0]))
        rank = ", ".join("%s: %s%%" % (label, round(coef * 100 / total_sum, 2))
                         for coef, label in result)
        return rank

    def _get_value_from_knobs(self, kv):
        x_each = []
        for p_nob in self.knobs:
            if p_nob['name'] not in kv.keys():
                raise ValueError("the param {} is not in the x0 ref".format(p_nob['name']))
            if p_nob['dtype'] == 'int':
                x_each.append(int(kv[p_nob['name']]))
            elif p_nob['dtype'] == 'float':
                x_each.append(float(kv[p_nob['name']]))
            else:
                x_each.append(kv[p_nob['name']])
        return x_each

    def transfer(self):
        """transfer ref x0 to int, y0 to float"""
        list_ref_x = []
        list_ref_y = []
        if self.x_ref is None or self.y_ref is None:
            return (list_ref_x, list_ref_y)

        for x_value in self.x_ref:
            kv = {}
            if len(x_value) != len(self.knobs):
                raise ValueError("x0 is not the same length with knobs")

            for val in x_value:
                params = val.split("=")
                if len(params) != 2:
                    raise ValueError("the param format of {} is not correct".format(params))
                kv[params[0]] = params[1]

            ref_x = self._get_value_from_knobs(kv)
            if len(ref_x) != len(self.knobs):
                raise ValueError("tuning parameter is not the same length with knobs")
            list_ref_x.append(ref_x)
        list_ref_y = [float(y) for y in self.y_ref]
        return (list_ref_x, list_ref_y)

    def run(self):
        """start the tuning process"""

        def objective(var):
            """objective method receive the benchmark result and send the next parameters"""
            iter_result = {}
            option = []
            for i, knob in enumerate(self.knobs):
                params[knob['name']] = var[i]
                if knob['dtype'] == 'string':
                    option.append(knob['options'].index(var[i]))
                else:
                    option.append(var[i])

            iter_result["param"] = params
            self.child_conn.send(iter_result)
            result = self.child_conn.recv()
            x_num = 0.0
            eval_list = result.split(',')
            for value in eval_list:
                num = float(value)
                x_num = x_num + num
            options.append(option)
            performance.append(x_num)
            return x_num

        utils.change_file_name()

        params = {}
        options = []
        performance = []
        labels = []
        estimator = None

        parameters = ""
        for knob in self.knobs:
            parameters += knob["name"] + ","
        utils.add_data_to_file(parameters[:-1], "w", self.project_name)

        try:
            if self.engine == 'random' or self.engine == 'forest' or \
                    self.engine == 'gbrt' or self.engine == 'bayes' or self.engine == 'extraTrees':
                params_space = self.build_space()
                ref_x, ref_y = self.transfer()
                if len(ref_x) == 0:
                    if len(self.ref) == 0:
                        ref_x = None
                    else:
                        ref_x = self.ref
                    ref_y = None
                if ref_x is not None and not isinstance(ref_x[0], (list, tuple)):
                    ref_x = [ref_x]
                LOGGER.info('x0: %s', ref_x)
                LOGGER.info('y0: %s', ref_y)

                if ref_x is not None and isinstance(ref_x[0], (list, tuple)):
                    self._n_random_starts = 0 if len(ref_x) >= self._n_random_starts \
                        else self._n_random_starts - len(ref_x) + 1

                LOGGER.info('n_random_starts parameter is: %d', self._n_random_starts)
                LOGGER.info("Running performance evaluation.......")
                if self.engine == 'random':
                    estimator = 'dummy'
                elif self.engine == 'forest':
                    estimator = 'RF'
                elif self.engine == 'extraTrees':
                    estimator = 'ET'
                elif self.engine == 'gbrt':
                    estimator = 'GBRT'
                elif self.engine == 'bayes':
                    params_space = normalize_dimensions(params_space)
                    estimator = cook_estimator("GP", space=params_space, noise=self.noise)

                LOGGER.info("base_estimator is: %s", estimator)
                optimizer = baseOpt(
                    dimensions=params_space,
                    n_random_starts=self._n_random_starts,
                    random_state=1,
                    base_estimator=estimator
                )
                n_calls = self.max_eval
                # User suggested points at which to evaluate the objective first
                if ref_x and ref_y is None:
                    ref_y = list(map(objective, ref_x))
                    LOGGER.info("ref_y is: %s", ref_y)
                    n_calls -= len(ref_y)

                # Pass user suggested initialisation points to the optimizer
                if ref_x:
                    if not isinstance(ref_y, (collections.Iterable, numbers.Number)):
                        raise ValueError("`ref_y` should be an iterable or a scalar, "
                                         "got %s" % type(ref_y))
                    if len(ref_x) != len(ref_y):
                        raise ValueError("`ref_x` and `ref_y` should "
                                         "have the same length")
                    LOGGER.info("ref_x: %s", ref_x)
                    LOGGER.info("ref_y: %s", ref_y)
                    ret = optimizer.tell(ref_x, ref_y)

                for i in range(n_calls):
                    next_x = optimizer.ask()
                    LOGGER.info("next_x: %s", next_x)
                    LOGGER.info("Running performance evaluation.......")
                    next_y = objective(next_x)
                    LOGGER.info("next_y: %s", next_y)
                    ret = optimizer.tell(next_x, next_y)
                    LOGGER.info("finish (ref_x, ref_y) tell")

                    data = ""
                    for element in next_x:
                        data += str(element) + ","
                    data += str(abs(next_y))
                    utils.add_data_to_file(data, "a", self.project_name)

                utils.add_data_to_file("END", "a", self.project_name)

            elif self.engine == 'abtest':
                abtuning_manager = ABtestTuningManager(self.knobs, self.child_conn,
                                                       self.split_count)
                options, performance = abtuning_manager.do_abtest_tuning_abtest()
                params = abtuning_manager.get_best_params()
                # convert string option into index
                options = abtuning_manager.get_options_index(options)
            elif self.engine == 'lhs':
                knobsampling_manager = KnobSamplingManager(self.knobs, self.child_conn,
                                                           self.max_eval, self.split_count)
                options = knobsampling_manager.get_knob_samples()
                performance = knobsampling_manager.do_knob_sampling_test(options)
                params = knobsampling_manager.get_best_params(options, performance)
                options = knobsampling_manager.get_options_index(options)
            elif self.engine == 'tpe':
                tpe_opt = TPEOptimizer(self.knobs, self.child_conn, self.max_eval)
                best_params = tpe_opt.tpe_minimize_tuning()
                final_param = {}
                final_param["finished"] = True
                final_param["param"] = best_params
                self.child_conn.send(final_param)
                return best_params
            LOGGER.info("Minimization procedure has been completed.")
        except ValueError as value_error:
            LOGGER.error('Value Error: %s', repr(value_error))
            self.child_conn.send(value_error)
            return None
        except RuntimeError as runtime_error:
            LOGGER.error('Runtime Error: %s', repr(runtime_error))
            self.child_conn.send(runtime_error)
            return None
        except Exception as err:
            LOGGER.error('Unexpected Error: %s', repr(err))
            self.child_conn.send(Exception("Unexpected Error:", repr(err)))
            return None

        for i, knob in enumerate(self.knobs):
            if estimator is not None:
                params[knob['name']] = ret.x[i]
            labels.append(knob['name'])

        LOGGER.info("Optimized result: %s", params)
        LOGGER.info("The optimized profile has been generated.")
        final_param = {}
        if self.sel_feature is True:
            wefs = WeightedEnsembleFeatureSelector()
            rank = wefs.get_ensemble_feature_importance(options, performance, labels)
            final_param["rank"] = rank
            LOGGER.info("The feature importances of current evaluation are: %s", rank)

        final_param["param"] = params
        final_param["finished"] = True
        self.child_conn.send(final_param)
        return params

    def stop_process(self):
        """stop process"""
        self.child_conn.close()
        self.terminate()
