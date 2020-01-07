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
The sub class of the monitor, used to collect the memory numa info.
"""

import subprocess

from ..common import Monitor


class MemNuma(Monitor):
    """To collect the memory numa info"""
    _module = "MEM"
    _purpose = "NUMA"
    _option = "-H"

    def __init__(self, user=None):
        Monitor.__init__(self, user)
        self.__cmd = "numactl"

    def _get(self, _):
        output = subprocess.check_output("{cmd} {opt}".format(
            cmd=self.__cmd, opt=self._option).split())
        return output.decode()