# -*- coding: utf-8 -*-
"""QA 门禁自检样本：故意植入代码质量问题（警告级），验证 pr_gate.py 的 ruff+black 检测能力。

预期：
- ruff 命中：F401（未用导入）、F841（未用变量）、E711（== None）、E722（裸 except）
- black 命中：would reformat（空格/引号等格式问题）
- 不命中阻断级：语法合法（py_compile 通过）、无密钥特征
验证完成后应删除本文件。
"""

import os
import sys


def calcValue( x ):
    unused_var=123
    result = x*2+1
    if x == None:
        result = 0
    try:
        print( 'calc: '+str(result) )
    except:
        pass
    return  result


class dataProcess:
    def Run(self,name):
        value = calcValue( 10 )
        return True
