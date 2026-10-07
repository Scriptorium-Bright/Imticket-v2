package org.example.ticket.seat.repository;

import jakarta.persistence.LockModeType;
import jakarta.persistence.QueryHint;
import org.example.ticket.seat.domain.Seat;
import org.example.ticket.seat.dto.SeatResponse;
import org.springframework.data.jpa.repository.*;
import org.springframework.data.repository.query.Param;

import java.util.List;

public interface SeatRepository extends JpaRepository<Seat, Long> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "3000"))
    @Query("select s from Seat s where s.performanceTime.id = :performanceTimeId and s.id in :seatIds order by s.id")
    List<Seat> findByPerformanceTimeIdAndIdsForUpdate(@Param("performanceTimeId") Long performanceTimeId,
                                                       @Param("seatIds") List<Long> seatIds);

    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @QueryHints(@QueryHint(name = "jakarta.persistence.lock.timeout", value = "3000"))
    @Query("select s from Seat s where s.id in :seatIds order by s.id")
    List<Seat> findByIdsForUpdate(@Param("seatIds") List<Long> seatIds);

    @Query("select new org.example.ticket.seat.dto.SeatResponse(s.id, s.section, s.rowNumber, s.seatNumber, s.grade, s.price, s.status) " +
            "from Seat s where s.performanceTime.id = :performanceTimeId order by s.id")
    List<SeatResponse> findSeatMap(@Param("performanceTimeId") Long performanceTimeId);
}
