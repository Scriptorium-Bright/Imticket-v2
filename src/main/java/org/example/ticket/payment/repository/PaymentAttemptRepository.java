package org.example.ticket.payment.repository;

import org.example.ticket.payment.domain.PaymentAttempt;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface PaymentAttemptRepository extends JpaRepository<PaymentAttempt, Long> {
    Optional<PaymentAttempt> findTopByPaymentOrderIdOrderByIdDesc(Long paymentOrderId);
}
