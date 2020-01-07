#!/usr/bin/python3
# -*- coding: utf-8 -*-
# Copyright (c) 2019 Huawei Technologies Co., Ltd.
# A-Tune is licensed under the Mulan PSL v1.
# You can use this software according to the terms and conditions of the Mulan PSL v1.
# You may obtain a copy of Mulan PSL v1 at:
#     http://license.coscl.org.cn/MulanPSL
# THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY OR FIT FOR A PARTICULAR
# PURPOSE.
# See the Mulan PSL v1 for more details.
# Create: 2019-10-29

"""
Test case.
"""
from analysis.plugin.public import GetConfigError
from analysis.plugin.configurator.ulimit.ulimit import Ulimit


class TestUlimit:
    """ test ulimit"""
    user = "UT"
    key = "student.hard.nofile"

    def test_get_ulimit(self):
        """test get ulimit"""
        try:
            ulimit = Ulimit(self.user)
            ulimit.get(self.key)
            assert False
        except GetConfigError:
            assert True