package org.example.ticket.payment.domain;

import jakarta.persistence.*;
import lombok.*;

import java.time.LocalDateTime;

@Entity
@Table(name = "payment_attempt",
        uniqueConstraints = @UniqueConstraint(name = "uk_payment_provider_transaction",
                columnNames = "provider_transaction_id"))
@Getter
@NoArgsConstructor(access = AccessLevel.PROTECTED)
@AllArgsConstructor
@Builder
public class PaymentAttempt {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "payment_order_id", nullable = false)
    private PaymentOrder paymentOrder;
    @Column(nullable = false, length = 40)
    private String provider;
    @Enumerated(EnumType.STRING)
    @Column(nullable = false, length = 20)
    private PaymentAttemptStatus status;
    @Column(name = "provider_transaction_id", length = 120)
    private String providerTransactionId;
    @Column(name = "approved_at")
    private LocalDateTime approvedAt;

    public void verify(String transactionId, LocalDateTime approvedAt) {
        status = PaymentAttemptStatus.VERIFIED;
        providerTransactionId = transactionId;
        this.approvedAt = approvedAt;
    }
}
