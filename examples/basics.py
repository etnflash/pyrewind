# 기본 예제: 변수, 반복문, 리스트, 함수, 별칭(같은 객체)
def square(n):
    result = n * n
    return result


numbers = [1, 2, 3]
total = 0
for x in numbers:
    sq = square(x)
    total = total + sq
    print("x =", x, "제곱 =", sq)

print("합계:", total)

# 별칭: a 와 b 는 같은 리스트를 가리킨다 (#번호가 같음)
a = [10, 20]
b = a
b.append(30)
print(a)

# 딕셔너리로 글자 수 세기
counts = {}
for ch in "banana":
    counts[ch] = counts.get(ch, 0) + 1
print(counts)


class Account:
    def __init__(self, owner):
        self.owner = owner
        self.balance = 0

    def deposit(self, amount):
        self.balance += amount


acc = Account("민수")
acc.deposit(5000)
acc.deposit(3000)
