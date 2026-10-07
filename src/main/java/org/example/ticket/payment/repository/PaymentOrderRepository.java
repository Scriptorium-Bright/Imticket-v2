package org.example.ticket.payment.repository;

import jakarta.persistence.LockModeType;
import org.example.ticket.payment.domain.PaymentOrder;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface PaymentOrderRepository extends JpaRepository<PaymentOrder, Long> {
    Optional<PaymentOrder> findByMemberIdAndIdempotencyKey(Long memberId, String idempotencyKey);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select p from PaymentOrder p where p.id = :id")
    Optional<PaymentOrder> findByIdForUpdate(@Param("id") Long id);

    @Query("select p.reservation.id from PaymentOrder p where p.id = :id")
    Optional<Long> findReservationIdById(@Param("id") Long id);

    @Query("select p from PaymentOrder p join fetch p.reservation join fetch p.member where p.id = :id")
    Optional<PaymentOrder> findByIdWithOwner(@Param("id") Long id);
}
