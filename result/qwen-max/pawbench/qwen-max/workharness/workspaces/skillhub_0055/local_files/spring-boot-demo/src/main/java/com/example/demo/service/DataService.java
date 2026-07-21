package com.example.demo.service;

import com.alibaba.fastjson.JSON;
import com.alibaba.fastjson.TypeReference;
import org.springframework.stereotype.Service;

import java.io.ByteArrayInputStream;
import java.io.ObjectInputStream;
import java.util.List;
import java.util.Map;

/**
 * Data Service - 数据处理服务
 */
@Service
public class DataService {

    /**
     * 导入数据
     */
    public String importData(String jsonData) {
        try {
            Map<String, Object> data = JSON.parseObject(jsonData, new TypeReference<Map<String, Object>>() {});
            
            List<Map<String, Object>> users = (List<Map<String, Object>>) data.get("users");
            if (users != null) {
                for (Map<String, Object> user : users) {
                    processUser(user);
                }
            }
            
            return "Import successful, processed " + (users != null ? users.size() : 0) + " users";
        } catch (Exception e) {
            return "Import failed: " + e.getMessage();
        }
    }

    /**
     * 解析配置
     */
    public Map<String, Object> parseConfig(String configJson) {
        com.alibaba.fastjson.parser.ParserConfig config = com.alibaba.fastjson.parser.ParserConfig.getGlobalInstance();
        config.setSafeMode(false);
        config.setAutoTypeSupport(true);
        
        return JSON.parseObject(configJson, Map.class);
    }

    /**
     * 处理用户数据
     */
    private void processUser(Map<String, Object> user) {
        String username = (String) user.get("username");
        String email = (String) user.get("email");
        System.out.println("Processing user: " + username + ", " + email);
    }

    /**
     * 从字节数组反序列化对象
     */
    public Object deserializeObject(byte[] data) throws Exception {
        ByteArrayInputStream bais = new ByteArrayInputStream(data);
        ObjectInputStream ois = new ObjectInputStream(bais);
        return ois.readObject();
    }
}
