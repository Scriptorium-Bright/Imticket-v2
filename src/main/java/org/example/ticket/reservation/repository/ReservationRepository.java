package org.example.ticket.reservation.repository;

import jakarta.persistence.LockModeType;
import org.example.ticket.reservation.domain.Reservation;
import org.example.ticket.reservation.domain.ReservationStatus;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

public interface ReservationRepository extends JpaRepository<Reservation, Long> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select r from Reservation r where r.id = :id")
    Optional<Reservation> findByIdForUpdate(@Param("id") Long id);

    @Query("select r.id from Reservation r where r.status = :status and r.expiredAt < :now order by r.expiredAt")
    List<Long> findExpiredIds(@Param("status") ReservationStatus status,
                              @Param("now") LocalDateTime now,
                              Pageable pageable);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select r from Reservation r where r.id in :ids order by r.id")
    List<Reservation> findByIdsForUpdate(@Param("ids") List<Long> ids);
}
