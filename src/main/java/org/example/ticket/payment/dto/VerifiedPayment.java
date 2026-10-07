package org.example.ticket.payment.dto;
import java.time.LocalDateTime;
public record VerifiedPayment(String providerTransactionId, String merchantOrderId,
                              Integer amount, String currency, LocalDateTime approvedAt) {}
