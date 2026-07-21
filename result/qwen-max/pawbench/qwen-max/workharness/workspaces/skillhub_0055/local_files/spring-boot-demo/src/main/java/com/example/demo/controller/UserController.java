package com.example.demo.controller;

import com.example.demo.service.UserService;
import com.example.demo.service.DataService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * User Controller - 处理用户相关请求
 */
@RestController
@RequestMapping("/api/user")
public class UserController {

    @Autowired
    private UserService userService;

    @Autowired
    private DataService dataService;

    /**
     * 根据用户名查询用户
     */
    @GetMapping("/search")
    public Map<String, Object> searchUser(@RequestParam String username) {
        return userService.findUserByName(username);
    }

    /**
     * 根据 ID 查询用户
     */
    @GetMapping("/{id}")
    public Map<String, Object> getUserById(@PathVariable String id) {
        return userService.findUserById(id);
    }

    /**
     * 导入用户数据
     */
    @PostMapping("/import")
    public String importUsers(@RequestBody String jsonData) {
        return dataService.importData(jsonData);
    }

    /**
     * 批量查询用户
     */
    @PostMapping("/batch")
    public Map<String, Object> batchQuery(@RequestBody Map<String, Object> params) {
        String ids = (String) params.get("ids");
        return userService.batchFindUsers(ids);
    }
}
