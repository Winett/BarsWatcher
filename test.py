def main():
    n, m = map(int, input().split())
    matrix = [list(map(int, input().split())) for i in range(n)]
    dp = [[0 for i in range(m)] for j in range(n)]
    dp[0][0] = matrix[0][0]
    for i in range(1, m):
        dp[0][i] = dp[0][i - 1] + matrix[0][i]
    for i in range(1, n):
        dp[i][0] = dp[i - 1][0] + matrix[i][0]
    steps = []

    for i in range(1, n):
        for j in range(1, m):
            mx = max(dp[i - 1][j], dp[i][j - 1])
            if mx == dp[i - 1][j]:
                steps.append("D")
            else:
                steps.append("R")
            dp[i][j] = mx + matrix[i][j]
    print(dp[n - 1][m - 1])
    print(*steps, sep=" ")


if __name__ == '__main__':
    main()
