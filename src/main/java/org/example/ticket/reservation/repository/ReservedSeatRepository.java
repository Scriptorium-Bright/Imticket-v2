package org.example.ticket.reservation.repository;

import org.example.ticket.reservation.domain.ReservedSeat;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface ReservedSeatRepository extends JpaRepository<ReservedSeat, Long> {
    @Query("select rs.seat.id from ReservedSeat rs where rs.reservation.id in :reservationIds order by rs.seat.id")
    List<Long> findSeatIdsByReservationIds(@Param("reservationIds") List<Long> reservationIds);
}
