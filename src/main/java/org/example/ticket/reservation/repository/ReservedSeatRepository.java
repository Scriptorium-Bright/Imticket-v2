package org.example.ticket.reservation.repository;

import org.example.ticket.reservation.domain.ReservedSeat;
import org.example.ticket.seat.dto.SeatResponse;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface ReservedSeatRepository extends JpaRepository<ReservedSeat, Long> {
    @Query("select rs.seat.id from ReservedSeat rs where rs.reservation.id in :reservationIds order by rs.seat.id")
    List<Long> findSeatIdsByReservationIds(@Param("reservationIds") List<Long> reservationIds);

    @Query("select new org.example.ticket.seat.dto.SeatResponse(s.id, s.section, s.rowNumber, s.seatNumber, s.grade, s.price, s.status) " +
            "from ReservedSeat rs join rs.seat s where rs.reservation.id = :reservationId order by s.id")
    List<SeatResponse> findSeatResponsesByReservationId(@Param("reservationId") Long reservationId);
}
