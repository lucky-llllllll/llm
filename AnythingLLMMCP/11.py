class MyList:
    def __init__(self):
        self._data = []

    def length(self):
        return len(self._data)

    def empty(self):
        return len(self._data) == 0

    def clear(self):
        self._data.clear()

    def append(self, x):
        self._data.append(x)

    def prepend(self, x):
        self._data.insert(0, x)

    def insert(self, pos, x):
        if not (0 <= pos <= self.length()):
            raise IndexError("insert pos out of range")
        self._data.insert(pos, x)

    def pop(self, pos):
        if self.empty() or not (0 <= pos < self.length()):
            raise IndexError("pop pos out of range")
        return self._data.pop(pos)

    def remove(self, x):
        if x in self._data:
            self._data.remove(x)

    def get(self, pos):
        if not (0 <= pos < self.length()):
            raise IndexError("get pos out of range")
        return self._data[pos]

    def set(self, pos, x):
        if not (0 <= pos < self.length()):
            raise IndexError("set pos out of range")
        self._data[pos] = x

    def index(self, x):
        if x in self._data:
            return self._data.index(x)
        else:
            return -1  # 任务要求，找不到返回-1，不是抛错

    def contains(self, x):
        return x in self._data

    def items(self):
        return iter(self._data)


# 验收测试代码
def test_mylist():
    ml = MyList()
    print("=== 测试length、empty空表 ===")
    assert ml.length() == 0
    assert ml.empty() == True
    print("✓ 空表length empty通过")

    ml.append(5)
    assert ml.length() ==1
    assert ml.empty() == False
    print("✓ append空表追加")

    ml.prepend(9)
    assert list(ml.items()) == [9,5]
    print("✓ prepend表头插入")

    ml.insert(1,7)
    assert list(ml.items()) == [9,7,5]
    print("✓ insert中间位置")

    assert ml.index(7) ==1
    assert ml.index(99) == -1
    print("✓ index，不存在返回-1")

    assert ml.contains(5) == True
    assert ml.contains(100) == False
    print("✓ contains")

    val = ml.pop(0)
    assert val ==9
    assert list(ml.items()) == [7,5]
    print("✓ pop正常")

    ml.remove(7)
    assert list(ml.items()) == [5]
    print("✓ remove元素")

    ml.set(0, 10)
    assert ml.get(0) ==10
    print("✓ get set")

    ml.clear()
    assert ml.empty()
    print("✓ clear清空")

    # 边界异常测试
    try:
        ml.pop(0)
        assert False, "pop空表应该抛异常"
    except IndexError:
        print("✓ pop空表越界抛出异常")

    try:
        ml.insert(5, 100)
        assert False
    except IndexError:
        print("✓ insert越界抛异常")

    print("\n✅ 全部验收用例通过！")

if __name__ == "__main__":
    test_mylist()
