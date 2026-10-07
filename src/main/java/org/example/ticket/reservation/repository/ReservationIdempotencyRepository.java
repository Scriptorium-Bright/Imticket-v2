package org.example.ticket.reservation.repository;

import jakarta.persistence.LockModeType;
import org.example.ticket.reservation.domain.ReservationIdempotency;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;

import java.util.Optional;

public interface ReservationIdempotencyRepository extends JpaRepository<ReservationIdempotency, Long> {
    Optional<ReservationIdempotency> findByMemberIdAndIdempotencyKey(Long memberId, String idempotencyKey);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select i from ReservationIdempotency i where i.id = :id")
    Optional<ReservationIdempotency> findByIdForUpdate(@Param("id") Long id);
}
