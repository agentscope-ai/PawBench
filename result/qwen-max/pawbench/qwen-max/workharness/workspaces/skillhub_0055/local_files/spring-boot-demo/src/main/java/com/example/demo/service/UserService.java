package com.example.demo.service;

import com.example.demo.repository.UserRepository;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * User Service - 用户业务逻辑
 */
@Service
public class UserService {

    @Autowired
    private UserRepository userRepository;

    /**
     * 根据用户名查询用户
     */
    public Map<String, Object> findUserByName(String username) {
        String sql = "SELECT * FROM users WHERE username = '" + username + "'";
        List<Map<String, Object>> results = userRepository.executeQuery(sql);
        
        if (results.isEmpty()) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", "User not found");
            return error;
        }
        return results.get(0);
    }

    /**
     * 根据 ID 查询用户
     */
    public Map<String, Object> findUserById(String id) {
        String sql = "SELECT * FROM users WHERE id = " + id;
        List<Map<String, Object>> results = userRepository.executeQuery(sql);
        
        if (results.isEmpty()) {
            Map<String, Object> error = new HashMap<>();
            error.put("error", "User not found");
            return error;
        }
        return results.get(0);
    }

    /**
     * 批量查询用户
     */
    public Map<String, Object> batchFindUsers(String ids) {
        String sql = "SELECT * FROM users WHERE id IN (" + ids + ")";
        List<Map<String, Object>> results = userRepository.executeQuery(sql);
        
        Map<String, Object> response = new HashMap<>();
        response.put("count", results.size());
        response.put("data", results);
        return response;
    }

    /**
     * 更新用户信息
     */
    public boolean updateUser(String userId, String email, String phone) {
        String sql = "UPDATE users SET email = '" + email + "', phone = '" + phone + 
                     "' WHERE id = " + userId;
        return userRepository.executeUpdate(sql) > 0;
    }

    /**
     * 删除用户
     */
    public boolean deleteUser(String userId) {
        String sql = "DELETE FROM users WHERE id = " + userId;
        return userRepository.executeUpdate(sql) > 0;
    }
}
