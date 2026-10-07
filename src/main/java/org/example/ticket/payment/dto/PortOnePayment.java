package org.example.ticket.payment.dto;
import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.time.OffsetDateTime;
@JsonIgnoreProperties(ignoreUnknown = true)
public record PortOnePayment(String status, String id, String transactionId,
                             PortOneAmount amount, String currency, OffsetDateTime paidAt) {}
