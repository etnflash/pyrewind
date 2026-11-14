# 버그 찾기 연습: 점수 평균을 구하는데 결과가 이상하다?
# 단계를 넘기며 total 과 count 가 어떻게 바뀌는지 보세요.

def average(scores):
    total = 0
    count = 0
    for s in scores:
        if s < 0:
            continue
        total += s
    count += 1
    return total / count


def factorial(n):
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def best_score(scores):
    best = scores[0]
    for s in scores:
        if s > best:
            best = s
    return best


print("평균:", average([80, 90, 100]))   # 기대값 90 인데...?
print("5! =", factorial(5))
print("최고:", best_score([70, 95, 88]))
print("최고:", best_score([]))           # 여기서는 오류가 난다 - 왜일까?
