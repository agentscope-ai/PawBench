# House Robber Problem Explanation

## Problem Statement
You are a professional robber planning to rob houses along a street. Each house has a certain amount of money stashed, the only constraint stopping you from robbing each of them is that adjacent houses have security systems connected and it will automatically contact the police if two adjacent houses were broken into on the same night.

Given an integer array `nums` representing the amount of money of each house, return the maximum amount of money you can rob tonight without alerting the police.

## Why a Greedy Approach Fails
A greedy approach would fail because it does not take into account the overall structure of the problem. For example, if we have the input `[2, 1, 1, 2]`, a greedy algorithm might choose to rob the first and the last house, yielding a total of `4`. However, the optimal solution is to rob the second and fourth houses, also giving a total of `4`. This example does not show a failure, but for a case like `[2, 7, 9, 3, 1]`, a greedy approach that always takes the maximum available loot at each step would result in suboptimal solutions (e.g., taking 9 and then 1, instead of 7 and 3).

## Dynamic Programming Solution
We can solve this problem using dynamic programming. Let `dp[i]` represent the maximum amount of money that can be robbed up until the `i-th` house. The recurrence relation is as follows:
- `dp[0] = nums[0]` (if there's only one house, the maximum loot is the value of that house)
- `dp[1] = max(nums[0], nums[1])` (for two houses, the maximum loot is the greater value between the two)
- `dp[i] = max(dp[i - 1], dp[i - 2] + nums[i])` for `i > 1` (the maximum loot at the `i-th` house is either the loot from the previous house or the sum of the current house and the loot from the house before the previous one)

### Walkthrough Example
Let's walk through the DP solution with the input `[2, 7, 9, 3, 1]`:
- `dp[0] = 2`
- `dp[1] = max(2, 7) = 7`
- `dp[2] = max(7, 2 + 9) = 11`
- `dp[3] = max(11, 7 + 3) = 11`
- `dp[4] = max(11, 11 + 1) = 12`

The final answer is `dp[4] = 12`.

## Complexity Analysis
- **O(n)-space tabulation approach**: The time complexity is O(n) since we iterate through the entire list once. The space complexity is also O(n) due to the `dp` array.
- **Space-optimized O(1) approach**: We can optimize the space by using two variables to keep track of the previous two states, reducing the space complexity to O(1), while the time complexity remains O(n).

## Edge Cases
- An empty list `[]` should return `0` as there are no houses to rob.
- A single house `[x]` should return `x` as it's the only option.
- Two houses `[a, b]` should return `max(a, b)` as the best option is to rob the more valuable one.

This explanation should provide a good foundation for junior engineers to understand the House Robber problem and its solution.