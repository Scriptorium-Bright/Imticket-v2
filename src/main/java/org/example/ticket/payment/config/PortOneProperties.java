package org.example.ticket.payment.config;

import lombok.Getter;
import lombok.Setter;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Getter
@Setter
@ConfigurationProperties(prefix = "portone")
public class PortOneProperties {
    private String apiSecret = "";
    private String apiBaseUrl = "https://api.portone.io";
}
